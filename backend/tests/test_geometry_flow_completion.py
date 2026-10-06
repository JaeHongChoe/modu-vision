"""Native source geometry, learned alignment and calibrated measurements."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from backend.engine.flow_operators import apply_operator, local_image
from backend.engine.flowchart_engine import FlowchartEngine, FlowNode, FlowNodeData, FlowEdge, get_single_segmentation_flowchart


def test_fitted_oriented_roi_crops_native_axes_and_keeps_source_mapping():
    source = np.zeros((120, 140, 3), np.uint8)
    polygon = cv2.boxPoints(((70, 60), (48, 24), 30)).tolist()
    cv2.fillConvexPoly(source, np.rint(polygon).astype(np.int32), (180, 60, 20))
    roi = {'id': 'part', 'label': 'Part', 'bbox': [43, 37, 97, 83], 'polygon': polygon}
    result = apply_operator(source, [roi], 'preprocess', {'operation': 'fitted_roi'}, 'fit')[0]
    pixels, transform = local_image(source, result)
    assert pixels.shape[:2] == (24, 48)
    assert np.allclose((transform @ [23.5, 11.5, 1])[:2], [70, 60], atol=1e-4)
    assert np.linalg.norm(transform[:2, 0]) == pytest.approx(1)
    assert np.linalg.norm(transform[:2, 1]) == pytest.approx(1)
    assert np.median(pixels[3:-3, 3:-3, 0]) == 180
    assert result['polygon'] == polygon


def test_fitted_native_size_is_stable_at_large_source_coordinates():
    from backend.engine.flow_operators import fitted_roi_geometry
    polygon=cv2.boxPoints(((4096,2732),(48,24),33)).tolist()
    geometry=fitted_roi_geometry(polygon)
    assert geometry['output_size']==[48,24]
    assert np.allclose((geometry['source_transform'] @ [23.5,11.5,1])[:2],[4096,2732],atol=.001)


def test_curves_and_area_use_anisotropic_source_pixel_calibration():
    from backend.engine.geometry_measurement import measure_geometry
    result = measure_geometry(
        {'calibration': {'unit': 'mm', 'mm_per_pixel_x': 0.2, 'mm_per_pixel_y': 0.5, 'source_size': [100, 80]},
         'paths': [{'id': 'line', 'points': [[0, 0], [3, 0], [3, 4]], 'interpolation': 'polyline'},
                   {'id': 'curve', 'points': [[0, 0], [0, 4], [3, 4], [3, 0]], 'interpolation': 'bezier'}]},
        source_size=[100, 80], polygons=[{'id': 'part', 'points': [[10, 10], [30, 10], [30, 20], [10, 20]]}],
        masks=[{'id': 'class_2', 'mask': np.ones((3, 4), np.uint8)}],
    )
    rows = {row['id']: row for row in result}
    assert rows['line']['length_px'] == 7
    assert rows['line']['length'] == pytest.approx(2.6)
    assert rows['curve']['length'] > 3
    assert rows['part']['area_px'] == 200
    assert rows['part']['area'] == pytest.approx(20)
    assert rows['class_2']['area'] == pytest.approx(1.2)
    assert rows['class_2']['measurement_source'] == 'segmentation_mask'


@pytest.mark.parametrize('params', [
    {'calibration': {'unit': 'mm', 'mm_per_pixel_x': float('nan'), 'mm_per_pixel_y': 1, 'source_size': [10, 10]}},
    {'calibration': {'unit': 'mm', 'mm_per_pixel_x': 1, 'mm_per_pixel_y': 1, 'source_size': [9, 10]}},
    {'paths': [{'id': 'bad', 'points': [[1, 1], [float('inf'), 4]]}]},
    {'paths': [{'id': 'bad', 'points': [[1, 1], [2, 4]], 'interpolation': 'bezier'}]},
])
def test_measurement_rejects_invalid_or_mismatched_source_geometry(params):
    from backend.engine.geometry_measurement import measure_geometry
    with pytest.raises(ValueError):
        measure_geometry(params, source_size=[10, 10])


def test_learned_rotation_flow_uses_checkpoint_and_inverse_source_transform(tmp_path):
    from backend.engine.rotation import RotationNet, ARCHITECTURE
    from backend.engine.flow_package import _model_jobs
    model = RotationNet(width=8)
    with torch.no_grad():
        for parameter in model.parameters(): parameter.zero_()
        model.head[-1].bias[:] = torch.tensor([0., 1.])
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'task': 'rotation', 'version': 1, 'architecture': ARCHITECTURE, 'width': 8,
                'image_size': 32, 'model_state_dict': model.state_dict()}, checkpoint)
    graph = get_single_segmentation_flowchart('job_segment')
    rotation = FlowNode(id='rotate', position={}, data=FlowNodeData(label='Learned alignment', node_type='preprocess',
        model_job_id='a' * 32, params={'operation': 'learned_rotation'}))
    graph.nodes.insert(1, rotation)
    graph.edges[0].target = 'rotate'
    graph.edges.insert(1, FlowEdge(id='rot-seg', source='rotate', target='node_inspect'))
    instance = FlowchartEngine(device='cpu', checkpoint_resolver=lambda *_: checkpoint)
    # The following segmentation fixture is a real pixel-conditioned network.
    class Raster(torch.nn.Module):
        def forward(self, value): return torch.cat([torch.zeros_like(value[:, :1]), value[:, :1] * 30 - 5], 1)
    instance._get_inspection_model = lambda **_: (Raster(), True)
    graph.nodes[2].data.crop_padding = 0
    graph.nodes[2].data.params = {'min_defect_area_px': 1}
    source = np.zeros((32, 48, 3), np.uint8); source[8:16, 20:28, 0] = 255
    result = instance.execute(pipeline=graph, image=source)
    artifact = next(step for step in result['execution_steps'] if step['node_id'] == 'rotate')['artifacts'][0]
    assert artifact['rotation']['correction_deg'] == pytest.approx(90)
    assert artifact['rotation']['checkpoint_sha256']
    assert artifact['image_size'] == [32, 48]
    assert _model_jobs(graph)['a' * 32] == 'rotation'
    assert result['final_verdict'] == 'NG'


def test_measurement_node_saves_path_and_mask_area_in_result(monkeypatch):
    from backend.tests.test_flow_class_evidence import engine, pipeline
    graph = pipeline()
    measure = FlowNode(id='measure', position={}, data=FlowNodeData(label='Size', node_type='measurement', params={
        'calibration': {'unit': 'mm', 'mm_per_pixel_x': .5, 'mm_per_pixel_y': .5, 'source_size': [32, 32]},
        'paths': [{'id': 'width', 'points': [[4, 4], [12, 4]]}], 'min_length': 5}))
    graph.nodes.insert(-2, measure)
    edge = next(e for e in graph.edges if e.source == 'node_inspect')
    edge.target = 'measure'
    graph.edges.append(FlowEdge(id='measure-decision', source='measure', target='node_decision'))
    image = np.zeros((32, 32, 3), np.uint8); image[4:12, 4:12, 0] = 255
    result = engine(monkeypatch).execute(pipeline=graph, image=image)
    rows = {r['id']: r for r in result['crops'][0]['measurements']}
    assert rows['width']['length'] == 4
    assert rows['width']['verdict'] == 'NG'
    assert rows['class_2']['area'] == 16
    assert result['final_verdict'] == 'REVIEW'
    assert result['crops'][0]['verdict'] == 'REVIEW'
    assert 'verified acquisition calibration' in result['rejection_reason']


def test_geometry_api_rejects_calibration_from_another_source(tmp_path):
    from fastapi import FastAPI
    import asyncio
    import httpx
    from PIL import Image
    from backend.api.routes_geometry import router
    app = FastAPI(); app.include_router(router)
    image = tmp_path / 'image.png'; Image.new('RGB', (32, 24), 'gray').save(image)
    body = {
        'image_path': str(image), 'params': {'calibration': {'unit': 'mm', 'mm_per_pixel_x': .1,
            'mm_per_pixel_y': .2, 'source_size': [64, 48]}, 'paths': [{'id': 'a', 'points': [[1, 1], [10, 10]]}]}}
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
            preview = await api.get('/api/geometry/source-preview', params={'image_path': str(image)})
            assert preview.status_code == 200, preview.text
            assert preview.json()['source_size'] == [32, 24]
            assert preview.json()['preview_data_url'].startswith('data:image/png;base64,')
            return await api.post('/api/geometry/measure', json=body)
    response = asyncio.run(request())
    assert response.status_code == 422


def test_independent_branches_execute_concurrently_with_stable_result_order():
    import threading
    import time
    graph = get_single_segmentation_flowchart('job_parallel')
    first = graph.nodes[1]
    second = first.model_copy(deep=True, update={'id': 'second'})
    graph.nodes.insert(2, second)
    graph.edges.extend([FlowEdge(id='in-second', source='node_input', target='second'),
                       FlowEdge(id='second-decision', source='second', target='node_decision')])
    graph = type(graph).model_validate({**graph.model_dump(), 'execution_config': {'max_workers': 2, 'device_slots': 2}})
    lock = threading.Lock(); state = {'active': 0, 'maximum': 0}
    class ParallelRaster(torch.nn.Module):
        def forward(self, value):
            with lock:
                state['active'] += 1; state['maximum'] = max(state['maximum'], state['active'])
            time.sleep(.15)
            with lock: state['active'] -= 1
            return torch.cat([torch.ones_like(value[:, :1]) * 8, torch.zeros_like(value[:, :1])], 1)
    instance = FlowchartEngine(device='cpu', max_device_concurrency=2)
    instance._get_inspection_model = lambda **_: (ParallelRaster(), True)
    result = instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))
    assert state['maximum'] == 2
    assert [c['roi_id'] for c in result['crops']] == ['node_inspect:full_image', 'second:full_image']
    assert [s['node_id'] for s in result['execution_steps']][:3] == ['node_input', 'node_inspect', 'second']
    assert result['execution_resources']['effective_device_slots'] == 2


def test_requested_parallel_device_slots_are_capped_by_engine_capacity():
    graph = get_single_segmentation_flowchart('job_parallel')
    graph = type(graph).model_validate({**graph.model_dump(), 'execution_config': {'max_workers': 4, 'device_slots': 4}})
    class Raster(torch.nn.Module):
        def forward(self, value): return torch.cat([torch.ones_like(value[:, :1]), torch.zeros_like(value[:, :1])], 1)
    instance = FlowchartEngine(device='cpu')
    instance._get_inspection_model = lambda **_: (Raster(), True)
    result = instance.execute(pipeline=graph, image=np.zeros((32, 32, 3), np.uint8))
    assert result['execution_resources']['effective_device_slots'] == 1


def test_learned_rotation_and_measurement_whole_flow_package_subprocess(tmp_path):
    import os
    import subprocess
    import sys
    from PIL import Image
    from backend.engine.rotation import RotationNet, ARCHITECTURE
    from backend.engine.segmentation.model import build_segmentation_model
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import compare_flow_results
    rotation = RotationNet(width=8)
    segmentation = build_segmentation_model('unet', num_classes=2, pretrained=False)
    with torch.no_grad():
        for parameter in rotation.parameters(): parameter.zero_()
        rotation.head[-1].bias[:] = torch.tensor([0., 1.])
        for parameter in segmentation.parameters(): parameter.zero_()
        segmentation.head.bias[1] = 8
    rotation_path = tmp_path / 'rotation' / 'best_model.pt'; rotation_path.parent.mkdir()
    segmentation_path = tmp_path / 'segmentation' / 'best_model.pt'; segmentation_path.parent.mkdir()
    torch.save({'task': 'rotation', 'version': 1, 'architecture': ARCHITECTURE, 'width': 8,
        'image_size': 32, 'model_state_dict': rotation.state_dict()}, rotation_path)
    torch.save({'task': 'segmentation', 'model_name': 'unet', 'classes': ['background', 'defect'],
        'image_size': [32, 32], 'model_state_dict': segmentation.state_dict()}, segmentation_path)
    graph = get_single_segmentation_flowchart('job_segment')
    graph.nodes[1].data.params = {'min_defect_area_px': 1}
    graph.nodes[1].data.crop_padding = 0
    graph.nodes.insert(1, FlowNode(id='rotation', position={}, data=FlowNodeData(label='Alignment', node_type='preprocess',
        model_job_id='b'*32, params={'operation': 'learned_rotation'})))
    graph.edges[0].target = 'rotation'
    graph.edges.insert(1, FlowEdge(id='rotation-model', source='rotation', target='node_inspect'))
    graph.nodes.insert(-2, FlowNode(id='measurement', position={}, data=FlowNodeData(label='Physical size', node_type='measurement',
        params={'calibration': {'unit': 'mm', 'mm_per_pixel_x': .1, 'mm_per_pixel_y': .2, 'source_size': [48, 32]},
                'paths': [{'id': 'edge', 'points': [[4, 4], [16, 4]]}]})))
    next(edge for edge in graph.edges if edge.source=='node_inspect').target='measurement'
    graph.edges.append(FlowEdge(id='measurement-decision', source='measurement', target='node_decision'))
    image_path = tmp_path / 'source.png'; Image.new('RGB', (48, 32), 'gray').save(image_path)
    checkpoints = {'b'*32: rotation_path, 'job_segment': segmentation_path}
    reference = FlowchartEngine(device='cpu', checkpoint_resolver=lambda job, _: checkpoints[job]).execute(pipeline=graph, image_path=str(image_path))
    package = Path(build_flow_package(pipeline=graph, checkpoints=checkpoints, output_base_dir=tmp_path/'exports', package_name='native_geometry')['package_path'])
    output = tmp_path/'result.json'
    process = subprocess.run([sys.executable, str(package/'run_flow.py'), '--image', str(image_path), '--output', str(output)],
        cwd=tmp_path, env={**os.environ,'PYTHONPATH':''}, capture_output=True, text=True, timeout=90)
    assert process.returncode==0,process.stderr
    packaged = json.loads(output.read_text())
    assert compare_flow_results(reference, packaged)['status']=='passed'
    assert packaged['crops'][0]['measurements']==reference['crops'][0]['measurements']
    assert packaged['crops'][0]['measurements'][0]['length']==pytest.approx(1.2)
    assert next(s for s in packaged['execution_steps'] if s['node_id']=='rotation')['artifacts'][0]['rotation']['correction_deg']==90


def test_engine_capacity_cannot_change_during_an_active_inference():
    import threading
    graph = get_single_segmentation_flowchart('job_running')
    started, release = threading.Event(), threading.Event()
    class WaitingRaster(torch.nn.Module):
        def forward(self, value):
            started.set(); assert release.wait(5)
            return torch.cat([torch.ones_like(value[:, :1]), torch.zeros_like(value[:, :1])], 1)
    instance = FlowchartEngine(device='cpu')
    instance._get_inspection_model = lambda **_: (WaitingRaster(), True)
    outcomes=[]
    runner = threading.Thread(target=lambda: outcomes.append(instance.execute(pipeline=graph, image=np.zeros((32,32,3),np.uint8))))
    runner.start()
    try:
        assert started.wait(5)
        with pytest.raises(RuntimeError, match='active|running'):
            instance.configure_execution_resources(device_slots=2)
        assert instance.execution_resources()['active_executions']==1
    finally:
        release.set(); runner.join(5)
    assert len(outcomes)==1
    configured = instance.configure_execution_resources(device_slots=1)
    assert configured['active_executions']==0 and configured['engine_device_capacity']==1


def test_explicit_cpu_resource_configuration_selects_cpu_without_gpu_work():
    instance=FlowchartEngine(device='cpu')
    configured=instance.configure_execution_resources(device_slots=1,device='cpu')
    assert configured['device']=='cpu' and configured['configuration_available'] is True
    with pytest.raises(ValueError,match='reservation|CPU'):
        instance.configure_execution_resources(device_slots=2,device='cuda')


def test_api_cpu_resources_configure_the_engine_used_by_explicit_local_execution(monkeypatch):
    from fastapi import FastAPI
    import asyncio
    import httpx
    from backend.api import routes_flowchart
    monkeypatch.setattr(routes_flowchart, '_CPU_ENGINE', FlowchartEngine(device='cpu'), raising=False)
    app = FastAPI(); app.include_router(routes_flowchart.router)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
            configured = await api.put('/api/flowchart/execution-resources', json={'device': 'cpu', 'device_slots': 2})
            return configured, await api.get('/api/flowchart/execution-resources?device=cpu')
    response, reopened = asyncio.run(exercise())
    assert response.status_code == 200, response.text
    assert reopened.json()['engine_device_capacity'] == 2
    assert routes_flowchart._local_execution_engine(torch.device('cpu')) is routes_flowchart._CPU_ENGINE


def test_gan_preparation_copies_source_and_rejects_later_original_change(tmp_path):
    from backend.engine.defect_gan import prepare_defect_gan_dataset, load_defect_gan_manifest
    source = tmp_path/'source'; source.mkdir()
    from PIL import Image
    Image.new('RGB',(64,64),'gray').save(source/'a.png')
    result = prepare_defect_gan_dataset(source,tmp_path/'prepared',[{'image':'a.png','bbox':[8,8,40,40],'split':'train','label':'scratch'}])
    assert not (source/'defect_gan.json').exists()
    manifest=load_defect_gan_manifest(result)
    assert manifest['source_dataset_path']==str(source.resolve())
    assert manifest['samples'][0]['label']=='scratch'
    assert manifest['source_map'][0]['source_image']=='a.png'
    assert Path(result,manifest['samples'][0]['image']).read_bytes()==(source/'a.png').read_bytes()
    Image.new('RGB',(64,64),'white').save(source/'a.png')
    with pytest.raises(ValueError,match='source.*changed|hash'):
        load_defect_gan_manifest(result)
