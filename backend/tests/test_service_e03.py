"""E03: a spatial calibration is an artifact with an identity, made from known lengths on a planar fixture (or kept as
an explicit manual scale), bound to the camera, its setup and the image size; a measurement's limits are compared in
their declared unit, and limits in mm never fall back to pixels when the calibration is missing or does not apply.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.engine.geometry_measurement import measure_geometry
from backend.engine.spatial_calibration import (CalibrationRejected, CalibrationScope, CalibrationStore, SpatialCalibration,
                                                acquisition_config_hash, calibrate_known_lengths, calibration_scope,
                                                manual_calibration, refusal)

SETUP = {'resolution': [1280, 960], 'lens': '16mm', 'working_distance_mm': 300, 'exposure_us': 800}
WHEN = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
SX, SY = 0.05, 0.07  # mm per pixel of the simulated plane


def segment(x1, y1, x2, y2, noise=0.0):
    return {'points': [[x1, y1], [x2, y2]], 'length_mm': float(np.hypot((x2 - x1) * SX, (y2 - y1) * SY)) + noise}


FIXTURE = [segment(100, 100, 900, 100), segment(100, 100, 100, 800), segment(100, 800, 900, 100, 0.004), segment(200, 700, 700, 200, -0.003)]


def calibrated(**overrides):
    values = dict(camera_id='line3-top', acquisition_config=SETUP, source_size=[1280, 960], tolerance_mm=0.05,
                  approved_by='QA lead', now=WHEN)
    return calibrate_known_lengths(FIXTURE, **{**values, **overrides})


def test_known_lengths_on_a_planar_fixture_give_both_scales_within_the_tolerance():
    calibration = calibrated()
    scales = calibration.scales_or_mapping
    assert abs(scales['mm_per_pixel_x'] - SX) < 1e-4 and abs(scales['mm_per_pixel_y'] - SY) < 1e-4
    assert calibration.method == 'known_length_planar' and 0 < calibration.residual < 0.01
    assert all(abs(row['error_mm']) <= 0.05 for row in calibration.evidence['segments'])
    assert calibration.acquisition_config_hash == acquisition_config_hash(SETUP)
    one = calibrated(isotropic=True, tolerance_mm=20.0)
    assert one.scales_or_mapping['mm_per_pixel_x'] == one.scales_or_mapping['mm_per_pixel_y']


def test_a_known_length_that_does_not_agree_refuses_the_whole_calibration():
    wrong = FIXTURE[:3] + [segment(200, 700, 700, 200, 0.4)]  # one fixture length entered 0.4 mm off
    with pytest.raises(CalibrationRejected) as refused:
        calibrate_known_lengths(wrong, camera_id='line3-top', acquisition_config=SETUP, source_size=[1280, 960],
                                tolerance_mm=0.05, approved_by='QA lead')
    assert max(abs(row['error_mm']) for row in refused.value.errors) > 0.05 and len(refused.value.errors) == 4
    for bad in (FIXTURE[:2],                                                   # nothing left to check the fit
                [segment(100, 100, 900, 100), segment(100, 300, 800, 310), segment(50, 500, 1000, 520)],  # one direction only
                FIXTURE[:3] + [{'points': [[0, 0], [5, 0]], 'length_mm': 0.25}],  # too short to measure
                FIXTURE[:3] + [{'points': [[0, 0], [2000, 0]], 'length_mm': 100}]):  # outside the image
        with pytest.raises(ValueError):
            calibrate_known_lengths(bad, camera_id='line3-top', acquisition_config=SETUP, source_size=[1280, 960],
                                    tolerance_mm=0.05, approved_by='QA lead')


def test_a_calibration_is_identified_by_its_content_and_stored_once(tmp_path):
    first = calibrated()
    assert first.ref == calibrated().ref and first.ref.startswith('spatial-cal:sha256:')
    for changed in (calibrated(camera_id='line3-side'), calibrated(acquisition_config={**SETUP, 'exposure_us': 900}),
                    calibrated(tolerance_mm=0.06), calibrated(now=datetime(2026, 10, 5, tzinfo=timezone.utc))):
        assert changed.ref != first.ref
    store = CalibrationStore(tmp_path / 'calibrations')
    assert store.save(first) == first.ref and store.save(first) == first.ref
    assert store.load(first.ref).to_json() == first.to_json()
    assert [row['ref'] for row in store.list()] == [first.ref]
    path = tmp_path / 'calibrations' / f"{first.ref.split(':')[-1]}.json"
    record = json.loads(path.read_text(encoding='utf-8'))
    record['scales_or_mapping']['mm_per_pixel_x'] = 0.06
    path.write_text(json.dumps(record), encoding='utf-8')
    assert store.load(first.ref) is None, 'a record changed after it was made is not used'
    with pytest.raises(ValueError):
        store.save(first)
    assert store.load('spatial-cal:sha256:' + '0' * 64) is None
    with pytest.raises(ValueError):
        SpatialCalibration.from_json({**first.to_json(), 'camera_id': 'other'})


def test_the_same_image_size_from_another_camera_or_setup_does_not_keep_millimetres():
    calibration = calibrated()
    same = {'camera_id': 'line3-top', 'acquisition_config_hash': acquisition_config_hash(SETUP)}
    assert refusal(calibration, source_size=[1280, 960], acquisition=same) is None
    assert refusal(calibration, source_size=[1280, 960]) is None, 'a run that does not know its camera is not refused'
    assert 'camera line3-top' in refusal(calibration, source_size=[1280, 960], acquisition={**same, 'camera_id': 'line4-top'})
    assert 'settings changed' in refusal(calibration, source_size=[1280, 960],
                                         acquisition={**same, 'acquisition_config_hash': acquisition_config_hash({**SETUP, 'lens': '25mm'})})
    assert '1280x960' in refusal(calibration, source_size=[640, 480], acquisition=same)


def test_limits_are_compared_in_their_unit_and_mm_limits_never_fall_back_to_pixels(tmp_path):
    store = CalibrationStore(tmp_path)
    calibration = calibrated()
    store.save(calibration)
    path = {'id': 'width', 'points': [[100, 100], [500, 100]]}  # 400 px = 20 mm on this plane
    params = {'calibration_ref': calibration.ref, 'threshold_unit': 'mm', 'paths': [path], 'min_length': 19.5, 'max_length': 20.5}
    with calibration_scope(store.load):
        [row] = measure_geometry(params, source_size=[1280, 960])
    assert row['unit'] == 'mm' and abs(row['length'] - 20.0) < 0.05 and row['length_px'] == 400 and row['verdict'] == 'OK'
    assert row['calibration']['ref'] == calibration.ref and row['calibration']['acquisition_verified'] is False
    assert row['threshold_unit'] == 'mm'
    camera = {'camera_id': 'line3-top', 'acquisition_config_hash': acquisition_config_hash(SETUP)}
    [checked] = measure_geometry(params, source_size=[1280, 960], scope=CalibrationScope(store.load, camera))
    assert checked['calibration']['acquisition_verified'] is True
    # Missing artifact, another camera, another image size: no millimetres, and mm limits cannot be judged.
    for scope, size in ((None, [1280, 960]), (CalibrationScope(lambda ref: None), [1280, 960]),
                        (CalibrationScope(store.load, {**camera, 'camera_id': 'line4-top'}), [1280, 960]),
                        (CalibrationScope(store.load), [640, 480])):
        with pytest.raises(ValueError, match='limits are in mm'):
            measure_geometry(params, source_size=size, scope=scope)
    # Limits in px are judged in px whatever the calibration does; millimetres are added when it applies.
    in_px = {**params, 'threshold_unit': 'px', 'min_length': 390, 'max_length': 410}
    [px_row] = measure_geometry(in_px, source_size=[1280, 960], scope=CalibrationScope(store.load))
    assert (px_row['verdict'], px_row['unit']) == ('OK', 'mm')
    [refused] = measure_geometry(in_px, source_size=[1280, 960], scope=CalibrationScope(lambda ref: None))
    assert (refused['verdict'], refused['unit'], refused['length']) == ('OK', 'px', 400.0)
    assert 'not available' in refused['calibration']['refused']
    area = {'calibration_ref': calibration.ref, 'threshold_unit': 'mm', 'min_area': 0, 'max_area': 40.0}
    [region] = measure_geometry(area, source_size=[1280, 960], polygons=[{'id': 'r', 'points': [[0, 0], [100, 0], [100, 100], [0, 100]]}],
                                scope=CalibrationScope(store.load))
    assert region['unit'] == 'mm2' and abs(region['area'] - 10000 * SX * SY) < 0.1 and region['verdict'] == 'OK'


def test_older_flows_keep_their_meaning_and_mm_limits_need_a_calibration():
    inline = {'calibration': {'unit': 'mm', 'mm_per_pixel_x': .5, 'mm_per_pixel_y': .5, 'source_size': [32, 32]},
              'paths': [{'id': 'width', 'points': [[4, 4], [12, 4]]}], 'min_length': 5}
    [row] = measure_geometry(inline, source_size=[32, 32])
    assert (row['unit'], row['length'], row['verdict'], row['threshold_unit']) == ('mm', 4.0, 'NG', 'mm')
    assert row['calibration']['method'] == 'inline_manual_scale' and row['calibration']['acquisition_verified'] is False
    [plain] = measure_geometry({'paths': [{'id': 'w', 'points': [[4, 4], [12, 4]]}], 'min_length': 5}, source_size=[32, 32])
    assert (plain['unit'], plain['verdict'], plain['threshold_unit']) == ('px', 'OK', 'px')
    for bad in ({'threshold_unit': 'mm', 'paths': []}, {'threshold_unit': 'inch', 'paths': []},
                {'calibration_ref': 'line3', 'paths': []},
                {'calibration_ref': 'spatial-cal:sha256:' + '0' * 64, 'calibration': inline['calibration'], 'paths': []}):
        with pytest.raises(ValueError):
            measure_geometry(bad, source_size=[32, 32])


def test_a_manual_scale_stays_an_explicit_unverified_method():
    manual = manual_calibration(0.1, 0.1, camera_id='line3-top', acquisition_config=SETUP, source_size=[1280, 960],
                                approved_by='QA lead', now=WHEN)
    assert manual.method == 'manual_planar_scale' and manual.residual is None and manual.evidence == {'verified': False}
    with pytest.raises(ValueError):
        manual_calibration(0, 0.1, camera_id='line3-top', acquisition_config=SETUP, source_size=[1280, 960], approved_by='QA lead')
    with pytest.raises(ValueError):
        manual_calibration(0.1, 0.1, camera_id='', acquisition_config=SETUP, source_size=[1280, 960], approved_by='QA lead')
    with pytest.raises(ValueError):
        manual_calibration(0.1, 0.1, camera_id='line3-top', acquisition_config={}, source_size=[1280, 960], approved_by='QA lead')


def bounded_calibration():
    return calibrate_known_lengths(
        [{'points': [[10, 10], [20, 10]], 'length_mm': 1},
         {'points': [[10, 10], [10, 20]], 'length_mm': 1}],
        camera_id='line3-top', acquisition_config=SETUP, source_size=[100, 100],
        tolerance_mm=.01, approved_by='QA lead', isotropic=True,
        valid_plane={'region': [10, 10, 30, 30], 'description': 'planar fixture'}, now=WHEN)


def bounded_scope(calibration):
    return CalibrationScope(lambda ref: calibration if ref == calibration.ref else None,
                            {'camera_id': 'line3-top', 'acquisition_config_hash': acquisition_config_hash(SETUP)})


@pytest.mark.parametrize('points,length', [
    ([[12, 12], [22, 12]], 1.0),
    ([[10, 10], [30, 10]], 2.0),  # continuous edges include both bounds of the half-open pixel region
    ([[10, 10], [10, 30]], 2.0),
])
def test_valid_plane_region_measures_inside_and_included_boundary_paths(points, length):
    calibration = bounded_calibration()
    [row] = measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm',
                             'paths': [{'id': 'width', 'points': points}], 'min_length': .9, 'max_length': 2},
                            source_size=[100, 100], scope=bounded_scope(calibration))
    assert (row['unit'], row['verdict']) == ('mm', 'OK')
    assert row['length'] == pytest.approx(length)
    assert row['calibration']['ref'] == calibration.ref
    assert row['calibration']['acquisition_verified'] is True


@pytest.mark.parametrize('points,limits_unit', [
    ([[70, 70], [90, 70]], 'mm'),  # original reproduction: 2 mm incorrectly accepted
    ([[9, 10], [20, 10]], 'mm'),
    ([[10, 9], [20, 10]], 'mm'),
    ([[10, 10], [30.001, 10]], 'mm'),  # just beyond the right continuous edge
    ([[10, 10], [10, 30.001]], 'mm'),  # just beyond the bottom continuous edge
    ([[70, 70], [90, 70]], 'px'),  # explicit pixel limits cannot silently keep a physical claim
])
def test_valid_plane_region_refuses_paths_without_silent_unit_fallback(points, limits_unit):
    calibration = bounded_calibration()
    with pytest.raises(ValueError, match='valid plane region'):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': limits_unit,
                          'paths': [{'id': 'width', 'points': points}]},
                         source_size=[100, 100], scope=bounded_scope(calibration))


def test_valid_plane_region_refuses_a_bezier_with_handles_outside_the_plane():
    calibration = bounded_calibration()
    with pytest.raises(ValueError, match='valid plane region'):
        measure_geometry({'calibration_ref': calibration.ref, 'paths': [
            {'id': 'curve', 'points': [[12, 12], [12, 70], [28, 70], [28, 12]], 'interpolation': 'bezier'}]},
            source_size=[100, 100], scope=bounded_scope(calibration))


@pytest.mark.parametrize('points,area', [
    ([[12, 12], [22, 12], [22, 22], [12, 22]], 1.0),
    ([[10, 10], [30, 10], [30, 30], [10, 30]], 4.0),  # polygon edges enclose half-open pixel cells
])
def test_valid_plane_region_measures_inside_and_boundary_polygon_cells(points, area):
    calibration = bounded_calibration()
    [row] = measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'},
                            source_size=[100, 100], polygons=[{'id': 'region', 'points': points}],
                            scope=bounded_scope(calibration))
    assert (row['unit'], row['verdict']) == ('mm2', 'OK')
    assert row['area'] == pytest.approx(area)


@pytest.mark.parametrize('points', [
    [[70, 70], [90, 70], [90, 90], [70, 90]],
    [[10, 10], [30, 10], [31, 20], [30, 30], [10, 30]],  # most vertices inside; one edge crosses
    [[9, 10], [20, 10], [20, 20], [9, 20]],
])
def test_valid_plane_region_refuses_outside_or_crossing_polygon(points):
    calibration = bounded_calibration()
    with pytest.raises(ValueError, match='valid plane region'):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'},
                         source_size=[100, 100], polygons=[{'id': 'region', 'points': points}],
                         scope=bounded_scope(calibration))


def test_valid_plane_region_checks_only_nonzero_mask_pixels_at_included_boundaries():
    calibration = bounded_calibration()
    mask = np.zeros((100, 100), np.uint8)
    mask[10:30, 10:30] = 1
    [row] = measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'},
                            source_size=[100, 100], masks=[{'id': 'pad', 'mask': mask}],
                            scope=bounded_scope(calibration))
    assert (row['area_px'], row['unit'], row['verdict']) == (400, 'mm2', 'OK')
    assert row['area'] == pytest.approx(4.0)


@pytest.mark.parametrize('outside', [(9, 10), (10, 9), (10, 30), (30, 10), (70, 70)])
def test_valid_plane_region_refuses_one_outside_mask_pixel_without_clipping(outside):
    calibration = bounded_calibration()
    mask = np.zeros((100, 100), np.uint8)
    mask[10:30, 10:30] = 1
    mask[outside] = 1
    with pytest.raises(ValueError, match='valid plane region'):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'},
                         source_size=[100, 100], masks=[{'id': 'pad', 'mask': mask}],
                         scope=bounded_scope(calibration))


def test_valid_plane_region_refuses_a_cropped_mask_with_no_source_coordinate_binding():
    calibration = bounded_calibration()
    with pytest.raises(ValueError, match='source.resolution'):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'},
                         source_size=[100, 100], masks=[{'id': 'pad', 'mask': np.ones((20, 20), np.uint8)}],
                         scope=bounded_scope(calibration))


@pytest.mark.parametrize('bbox', [[10, 10, 30, 30], [0, 0, 40, 40]])
def test_valid_plane_region_restores_bound_crop_mask_support_in_source_coordinates(bbox):
    calibration = bounded_calibration()
    x1, y1, x2, y2 = bbox
    mask = np.zeros((y2 - y1, x2 - x1), np.uint8)
    mask[10 - y1:30 - y1, 10 - x1:30 - x1] = 1
    [row] = measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'},
                            source_size=[100, 100], masks=[{'id': 'pad', 'mask': mask, 'bbox': bbox,
                            'source_transform': [[1, 0, x1], [0, 1, y1], [0, 0, 1]]}],
                            scope=bounded_scope(calibration))
    assert (row['area_px'], row['unit'], row['verdict']) == (400, 'mm2', 'OK')
    assert row['area'] == pytest.approx(4.0)
    assert row['bbox'] == bbox
    assert row['source_transform'] == [[1, 0, x1], [0, 1, y1], [0, 0, 1]]


def test_valid_plane_region_refuses_translated_crop_mask_pixels_beyond_its_boundary():
    calibration = bounded_calibration()
    mask = np.ones((20, 21), np.uint8)  # translated last column is source x=30, outside the plane
    with pytest.raises(ValueError, match='valid plane region'):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'}, source_size=[100, 100],
                         masks=[{'id': 'pad', 'mask': mask, 'bbox': [10, 10, 31, 30],
                                 'source_transform': [[1, 0, 10], [0, 1, 10], [0, 0, 1]]}],
                         scope=bounded_scope(calibration))


@pytest.mark.parametrize('transform', [
    [[2, 0, 10], [0, 1, 10], [0, 0, 1]],  # not an original-resolution raster
    [[0, -1, 10], [1, 0, 10], [0, 0, 1]],
    [[1, 0, 70], [0, 1, 10], [0, 0, 1]],  # transform contradicts the bbox
    [[1, 0, 10], [0, 1, 10], [.01, 0, 1]],
])
def test_valid_plane_region_refuses_unknown_or_inconsistent_crop_mask_transforms(transform):
    calibration = bounded_calibration()
    with pytest.raises(ValueError):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'}, source_size=[100, 100],
                         masks=[{'id': 'pad', 'mask': np.ones((20, 20), np.uint8), 'bbox': [10, 10, 30, 30],
                                 'source_transform': transform}], scope=bounded_scope(calibration))


@pytest.mark.parametrize('bbox', [[10, 10, 31, 30], [-1, 10, 19, 30]])
def test_valid_plane_region_refuses_crop_mask_bbox_shape_or_source_extent_mismatch(bbox):
    calibration = bounded_calibration()
    with pytest.raises(ValueError):
        measure_geometry({'calibration_ref': calibration.ref, 'threshold_unit': 'mm'}, source_size=[100, 100],
                         masks=[{'id': 'pad', 'mask': np.ones((20, 20), np.uint8), 'bbox': bbox,
                                 'source_transform': [[1, 0, bbox[0]], [0, 1, bbox[1]], [0, 0, 1]]}],
                         scope=bounded_scope(calibration))


def test_valid_plane_region_applies_to_referenced_manual_calibration_too():
    calibration = manual_calibration(.1, .1, camera_id='line3-top', acquisition_config=SETUP,
                                     source_size=[100, 100], approved_by='QA lead', now=WHEN,
                                     valid_plane={'region': [10, 10, 30, 30]})
    with pytest.raises(ValueError, match='valid plane region'):
        measure_geometry({'calibration_ref': calibration.ref, 'paths': [{'id': 'width', 'points': [[70, 70], [90, 70]]}]},
                         source_size=[100, 100], scope=bounded_scope(calibration))


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    return TestClient(app, headers={'X-Vision-Token': app.state.api_token})


def test_the_project_keeps_its_calibrations_and_a_flow_run_measures_with_them(tmp_path, monkeypatch):
    from backend.engine.flowchart_engine import FlowEdge, FlowNode, FlowNodeData
    from backend.tests.test_flow_class_evidence import engine, pipeline
    api = _app(tmp_path, monkeypatch)
    assert api.post('/api/project/create', json={'name': 'Line 3', 'task': 'segmentation'}).status_code == 200
    body = {'camera_id': 'line3-top', 'acquisition_config': SETUP, 'source_size': [1280, 960], 'segments': FIXTURE, 'tolerance_mm': 0.05}
    made = api.post('/api/geometry/calibrations/known-lengths', json=body)
    assert made.status_code == 200, made.text
    ref = made.json()['ref']
    assert made.json()['approved_by'] == 'this computer'
    assert [row['ref'] for row in api.get('/api/geometry/calibrations').json()['calibrations']] == [ref]
    assert api.get(f'/api/geometry/calibrations/{ref}').json()['scales_or_mapping'] == made.json()['scales_or_mapping']
    assert api.get('/api/geometry/calibrations/spatial-cal:sha256:' + '1' * 64).status_code == 404
    refused = api.post('/api/geometry/calibrations/known-lengths', json={**body, 'segments': FIXTURE[:3] + [segment(200, 700, 700, 200, 0.4)]})
    assert refused.status_code == 422 and len(refused.json()['detail']['segments']) == 4
    manual = api.post('/api/geometry/calibrations/manual', json={'camera_id': 'line3-top', 'acquisition_config': SETUP,
                                                                 'source_size': [1280, 960], 'mm_per_pixel_x': .1, 'mm_per_pixel_y': .1})
    assert manual.status_code == 200 and manual.json()['method'] == 'manual_planar_scale'
    project = api.get('/api/project/current').json()
    from pathlib import Path
    assert (Path(project['project_dir']) / 'calibrations' / f"{ref.split(':')[-1]}.json").is_file(), 'kept in the project folder'
    # A measurement node referencing the calibration: judged in mm within the project's scope, REVIEW outside it.
    graph = pipeline()
    measure = FlowNode(id='measure', position={}, data=FlowNodeData(label='Size', node_type='measurement', params={
        'calibration_ref': ref, 'threshold_unit': 'mm', 'paths': [{'id': 'width', 'points': [[100, 100], [500, 100]]}],
        'min_length': 19.5, 'max_length': 20.5}))
    graph.nodes.insert(-2, measure)
    edge = next(e for e in graph.edges if e.source == 'node_inspect')
    edge.target = 'measure'
    graph.edges.append(FlowEdge(id='measure-decision', source='measure', target='node_decision'))
    image = np.zeros((960, 1280, 3), np.uint8)
    image[100:300, 100:300, 0] = 255
    from backend.engine.spatial_calibration import project_calibration_store
    with calibration_scope(project_calibration_store(project).load):
        inside = engine(monkeypatch).execute(pipeline=graph, image=image)
    widths = [row for crop in inside['crops'] for row in crop.get('measurements') or [] if row['id'] == 'width']
    assert widths and widths[0]['unit'] == 'mm' and widths[0]['verdict'] == 'OK', inside['rejection_reason']
    outside = engine(monkeypatch).execute(pipeline=graph, image=image)
    assert outside['final_verdict'] == 'REVIEW' and 'limits are in mm' in outside['rejection_reason']


def test_a_flow_package_carries_its_calibration_and_measures_with_it(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    import torch
    from PIL import Image
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import FlowEdge, FlowNode, FlowNodeData
    from backend.engine.segmentation.model import build_segmentation_model
    from backend.tests.test_flow_class_evidence import pipeline
    model = build_segmentation_model('unet', num_classes=3, pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias[2] = 8
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'task': 'segmentation', 'model_name': 'unet', 'preset': 'fast', 'classes': ['background', 'pad', 'crack'],
                'image_size': [32, 32], 'model_state_dict': model.state_dict()}, checkpoint)
    store = CalibrationStore(tmp_path / 'project_calibrations')
    calibration = calibrate_known_lengths([segment(0, 0, 40, 0), segment(0, 0, 0, 30), segment(0, 30, 40, 0)], camera_id='line3-top',
                                          acquisition_config=SETUP, source_size=[48, 32], tolerance_mm=0.01, approved_by='QA lead', now=WHEN)
    store.save(calibration)
    graph = pipeline()
    measure = FlowNode(id='measure', position={}, data=FlowNodeData(label='Size', node_type='measurement', params={
        'calibration_ref': calibration.ref, 'threshold_unit': 'mm', 'paths': [{'id': 'width', 'points': [[4, 4], [44, 4]]}],
        'min_length': 1.9, 'max_length': 2.1}))
    graph.nodes.insert(-2, measure)
    edge = next(e for e in graph.edges if e.target == 'node_decision')
    edge.target = 'measure'
    graph.edges.append(FlowEdge(id='measure-decision', source='measure', target='node_decision'))
    with pytest.raises(ValueError, match='which this project does not have'):
        build_flow_package(pipeline=graph, checkpoints={'job_raster': checkpoint}, output_base_dir=tmp_path / 'refused', package_name='none',
                           calibrations=CalibrationStore(tmp_path / 'empty').load)
    built = build_flow_package(pipeline=graph, checkpoints={'job_raster': checkpoint}, output_base_dir=tmp_path / 'export',
                               package_name='measured', calibrations=store.load)
    package = Path(built['package_path'])
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['calibrations'] == [calibration.ref]
    assert f"calibrations/{calibration.ref.split(':')[-1]}.json" in {row['path'] for row in manifest['files']}
    image = tmp_path / 'part.png'
    Image.fromarray(np.full((32, 48, 3), 140, np.uint8)).save(image)

    def run(folder):
        output = tmp_path / f'{folder.name}.json'
        done = subprocess.run([sys.executable, str(folder / 'run_flow.py'), '--image', str(image), '--output', str(output)],
                              cwd=tmp_path, env={**os.environ, 'PYTHONPATH': ''}, capture_output=True, text=True, timeout=120)
        return done, output
    done, output = run(package)
    assert done.returncode == 0, done.stderr
    rows = [row for crop in json.loads(output.read_text(encoding='utf-8'))['crops'] for row in crop.get('measurements') or [] if row['id'] == 'width']
    assert rows and rows[0]['unit'] == 'mm' and abs(rows[0]['length'] - 2.0) < 1e-6 and rows[0]['calibration']['ref'] == calibration.ref
    # A package whose calibration was changed after it was built is refused before anything runs.
    changed = tmp_path / 'changed'
    shutil.copytree(package, changed)
    record_path = changed / 'calibrations' / f"{calibration.ref.split(':')[-1]}.json"
    record = json.loads(record_path.read_text(encoding='utf-8'))
    record['scales_or_mapping']['mm_per_pixel_x'] = 0.06
    record_path.write_text(json.dumps(record), encoding='utf-8')
    done, _ = run(changed)
    assert done.returncode != 0 and 'checksum mismatch' in done.stderr.lower()


def region_measurement_graph(calibration, *, outside_path=False, outside_mask=False):
    from backend.engine.flowchart_engine import FlowEdge, FlowNode, FlowNodeData
    from backend.tests.test_flow_class_evidence import pipeline
    graph = pipeline(roi=[70, 70, 90, 90] if outside_mask else [10, 10, 30, 30])
    measure = FlowNode(id='measure', position={}, data=FlowNodeData(label='Size', node_type='measurement', params={
        'calibration_ref': calibration.ref, 'threshold_unit': 'mm',
        'paths': [{'id': 'width', 'points': [[70, 70], [90, 70]] if outside_path else [[10, 10], [29, 10]]}],
        'min_length': 1.8, 'max_length': 2.1, 'max_area': 5}))
    graph.nodes.insert(-2, measure)
    next(edge for edge in graph.edges if edge.source == 'node_inspect').target = 'measure'
    graph.edges.append(FlowEdge(id='measure-decision', source='measure', target='node_decision'))
    return graph


@pytest.mark.parametrize('failure', ['path', 'mask'])
def test_valid_plane_region_failure_makes_the_flow_incomplete_without_measurement_rows(monkeypatch, failure):
    from backend.tests.test_flow_class_evidence import engine
    calibration = bounded_calibration()
    graph = region_measurement_graph(calibration, outside_path=failure == 'path', outside_mask=failure == 'mask')
    image = np.zeros((100, 100, 3), np.uint8)
    image[:, :, 0] = 255
    with calibration_scope(bounded_scope(calibration).resolve, bounded_scope(calibration).acquisition):
        result = engine(monkeypatch).execute(pipeline=graph, image=image)
    assert result['final_verdict'] == 'REVIEW', result['rejection_reason']
    assert 'valid plane region' in result['rejection_reason']
    step = next(step for step in result['execution_steps'] if step['node_id'] == 'measure')
    assert step['branch_verdict'] == 'REVIEW'
    assert not [row for crop in result['crops'] for row in crop.get('measurements') or []]


@pytest.mark.parametrize('failure', [None, 'path', 'mask'], ids=['inside', 'outside-path', 'outside-mask'])
def test_valid_plane_region_is_enforced_by_the_isolated_flow_package(tmp_path, failure):
    import os
    import subprocess
    import sys
    from pathlib import Path
    import torch
    from PIL import Image
    from backend.engine.flow_package import build_flow_package
    from backend.engine.segmentation.model import build_segmentation_model
    model = build_segmentation_model('unet', num_classes=3, pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.head.bias[2] = 8
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'task': 'segmentation', 'model_name': 'unet', 'preset': 'fast', 'classes': ['background', 'pad', 'crack'],
                'image_size': [32, 32], 'model_state_dict': model.state_dict()}, checkpoint)
    calibration = bounded_calibration()
    store = CalibrationStore(tmp_path / 'project_calibrations')
    store.save(calibration)
    graph = region_measurement_graph(calibration, outside_path=failure == 'path', outside_mask=failure == 'mask')
    built = build_flow_package(pipeline=graph, checkpoints={'job_raster': checkpoint},
                              output_base_dir=tmp_path / 'export', package_name='plane', calibrations=store.load)
    package = Path(built['package_path'])
    saved = SpatialCalibration.from_json(json.loads(
        (package / 'calibrations' / f"{calibration.ref.split(':')[-1]}.json").read_text(encoding='utf-8')))
    assert saved.to_json() == calibration.to_json()
    image = tmp_path / 'part.png'
    Image.new('RGB', (100, 100), 'gray').save(image)
    output = tmp_path / 'result.json'
    done = subprocess.run([sys.executable, str(package / 'run_flow.py'), '--image', str(image), '--output', str(output)],
                          cwd=tmp_path, env={**os.environ, 'PYTHONPATH': ''}, capture_output=True, text=True, timeout=90)
    assert done.returncode == 0, done.stderr
    result = json.loads(output.read_text(encoding='utf-8'))
    rows = [row for crop in result['crops'] for row in crop.get('measurements') or []]
    if failure:
        assert result['final_verdict'] == 'REVIEW', result['rejection_reason']
        assert 'valid plane region' in result['rejection_reason']
        assert not rows, 'a refused domain cannot keep physical values or an OK measurement'
    else:
        # The standalone CLI has no observed acquisition metadata. Geometry
        # stays available, but physical limits cannot approve this acquisition.
        assert result['final_verdict'] == 'REVIEW', result['rejection_reason']
        assert 'verified acquisition' in result['rejection_reason']
        width = next(row for row in rows if row['id'] == 'width')
        area = next(row for row in rows if row.get('class_id') == 2)
        assert (width['unit'], width['verdict']) == ('mm', 'OK')
        assert width['length'] == pytest.approx(1.9)
        assert (area['unit'], area['area_px'], area['verdict']) == ('mm2', 400, 'OK')
        assert area['area'] == pytest.approx(4)
        assert width['calibration']['ref'] == calibration.ref
