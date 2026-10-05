"""Learned direction, circular errors and bidirectional native-pixel contracts."""
import numpy as np
import pytest
import torch

from backend.engine.rotation import RotationNet, ARCHITECTURE, _metrics, predict_rotation_array, export_rotation_package, run_rotation_package


def constant_checkpoint(root, angle):
    model = RotationNet(8)
    with torch.no_grad():
        for parameter in model.parameters(): parameter.zero_()
        model.head[-1].bias[:] = torch.tensor([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
    root.mkdir()
    path = root / 'best_model.pt'
    torch.save({'task': 'rotation', 'version': 1, 'architecture': ARCHITECTURE, 'width': 8,
                'image_size': 32, 'model_state_dict': model.state_dict()}, path)
    return path


@pytest.mark.parametrize('angle', [0, 90, 180, -90, 37])
def test_direction_and_inverse_coordinate_recipe_matches_offline_pixels(tmp_path, angle):
    checkpoint = constant_checkpoint(tmp_path / 'candidate', angle)
    image = np.arange(31 * 47 * 3, dtype=np.uint8).reshape(31, 47, 3)
    reference = predict_rotation_array(checkpoint, image)
    assert abs((reference['correction_deg'] - angle + 180) % 360 - 180) < 1e-4
    assert reference['alignment_recipe'] == {
        'method': 'learned_direction', 'angle_range_deg': [-180, 180],
        'angle_semantics': 'counterclockwise_upright_correction_degrees_360',
        'interpolation': 'bilinear', 'coordinate_map': {'source_to_aligned': 'transform', 'aligned_to_source': 'inverse_transform'},
    }
    forward, inverse = reference['transform'], reference['inverse_transform']
    np.testing.assert_allclose(inverse @ forward, np.eye(3), atol=1e-7)
    # ROI / detector box / OCR polygon corners use the same pixel-center map.
    corners = np.array([[3, 21, 21, 3], [5, 5, 19, 19], [1, 1, 1, 1]], float)
    np.testing.assert_allclose(inverse @ (forward @ corners), corners, atol=1e-6)
    package = export_rotation_package(checkpoint, tmp_path / 'package')
    deployed = run_rotation_package(package, image)
    assert deployed['alignment_recipe'] == reference['alignment_recipe']
    np.testing.assert_allclose(deployed['inverse_transform'], inverse, atol=1e-6)
    np.testing.assert_array_equal(deployed['aligned_image'], reference['aligned_image'])
    if angle == 180:
        np.testing.assert_array_equal(reference['aligned_image'], image[::-1, ::-1])


def test_undefined_direction_cannot_be_reported_as_an_angle_evaluation():
    model = RotationNet(8)
    for parameter in model.parameters(): parameter.data.zero_()
    sample = [(torch.zeros(3, 32, 32), torch.tensor([1., 0.]))]
    with pytest.raises(ValueError, match='undefined'):
        _metrics(model, sample, 'cpu')


def test_circular_direction_error_crosses_wrap_without_axial_collapse():
    class Fixed(torch.nn.Module):
        def forward(self, images):
            radians = torch.deg2rad(torch.tensor([179., 180.]))
            return torch.stack([radians.cos(), radians.sin()], 1)
    truth = (-179., 0.)
    samples = [(torch.zeros(3, 32, 32), torch.tensor([np.cos(np.deg2rad(a)), np.sin(np.deg2rad(a))], dtype=torch.float32)) for a in truth]
    result = _metrics(Fixed(), samples, 'cpu')
    assert result['angular_mae_deg'] == pytest.approx(91, abs=.0001)
    assert result['within_10_deg'] == .5


@pytest.mark.parametrize('angle', [0,90,180,-90,37])
def test_roi_boundary_maps_are_invertible_and_masks_use_pixel_centers(tmp_path,angle):
    import cv2
    from backend.engine.flow_operators import source_boundary_points, source_bbox
    checkpoint=constant_checkpoint(tmp_path/'candidate',angle)
    pixels=np.zeros((24,40,3),np.uint8);pixels[5:10,7:14]=255
    predicted=predict_rotation_array(checkpoint,pixels)
    forward,inverse=predicted['transform'],predicted['inverse_transform']
    edges=np.array([[7,5],[14,5],[14,10],[7,10]],float)
    aligned=source_boundary_points(forward,edges)
    np.testing.assert_allclose(source_boundary_points(inverse,aligned),edges,atol=1e-6)
    if angle in (0,90,180,-90):
        assert source_bbox(inverse,*predicted['output_size'])==[0,0,40,24]
        mask=np.any(predicted['aligned_image']>0,axis=2).astype(np.uint8)
        restored=cv2.warpPerspective(mask,inverse,(40,24),flags=cv2.INTER_NEAREST)
        np.testing.assert_array_equal(restored,np.any(pixels>0,axis=2))


@pytest.mark.parametrize('family', ['ocr', 'rotated_detection'])
def test_aligned_roi_text_and_box_match_fresh_whole_flow_package(tmp_path, family):
    """Controlled saved networks exercise real dispatch, geometry and package code."""
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from PIL import Image
    from backend.engine.flowchart_engine import FlowchartEngine, FlowNode, FlowNodeData, FlowEdge, get_single_segmentation_flowchart
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import compare_flow_results
    rotation = constant_checkpoint(tmp_path / 'rotation', 90)
    if family == 'ocr':
        from backend.engine.ocr import SmallCTCOCR
        model = SmallCTCOCR(1)
        for parameter in model.parameters(): parameter.data.zero_()
        model.head.bias.data[1] = 8
        payload = {'task': 'ocr', 'version': 1, 'architecture': 'small_cnn_bigru_ctc',
                   'alphabet': 'A', 'image_size': [32, 32], 'model_state_dict': model.state_dict()}
    else:
        from backend.engine.rotated_detection import RotatedBoxNet
        model = RotatedBoxNet()
        for parameter in model.parameters(): parameter.data.zero_()
        model.head[-1].bias.data[:] = torch.tensor([0., 0., -1., -1., 0., 1.])
        payload = {'task': family, 'class_name': 'defect', 'image_size': 64, 'model_state_dict': model.state_dict()}
    candidate = tmp_path / 'candidate'; candidate.mkdir(); checkpoint = candidate / 'best_model.pt'; torch.save(payload, checkpoint)
    if family == 'rotated_detection':
        import hashlib
        (candidate/'model_meta.json').write_text(json.dumps({'task': family, 'class_name': 'defect', 'image_size': 64,
            'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest()}))
    graph = get_single_segmentation_flowchart('job_geometry')
    graph.nodes[1].data.task = family; graph.nodes[1].data.crop_padding = 0; graph.nodes[1].data.params = {'expected_text': 'A'} if family == 'ocr' else {}
    graph.nodes.insert(1, FlowNode(id='roi', position={}, data=FlowNodeData(label='Native ROI', node_type='fixed_roi', params={'roi_bbox': [3, 4, 43, 28]})))
    graph.nodes.insert(2, FlowNode(id='align', position={}, data=FlowNodeData(label='Learned direction', node_type='preprocess', model_job_id='a'*32, params={'operation': 'learned_rotation'})))
    graph.edges[0].target = 'roi'; graph.edges.insert(1, FlowEdge(id='roi-align', source='roi', target='align')); graph.edges.insert(2, FlowEdge(id='align-inspect', source='align', target='node_inspect'))
    image = tmp_path / 'source.png'; Image.fromarray(np.arange(32*48*3, dtype=np.uint8).reshape(32,48,3)).save(image)
    checkpoints = {'a'*32: rotation, 'job_geometry': checkpoint}
    reference = FlowchartEngine(device='cpu', checkpoint_resolver=lambda job, _: checkpoints[job]).execute(pipeline=graph, image_path=str(image))
    assert reference['crops']
    row = reference['crops'][0]
    if family == 'ocr':
        assert row['recognized_text'] == 'A'
        np.testing.assert_allclose(row['ocr_regions'][0]['polygon'], [[43,4],[43,28],[3,28],[3,4]], atol=1e-6)
        assert row['ocr_regions'][0]['box'] == [3,4,43,28]
        assert row['bbox'] == [3,4,43,28]
    else:
        assert len(row['polygon']) == 4
        assert all(3 <= x <= 43 and 4 <= y <= 28 for x,y in row['polygon'])
    package = Path(build_flow_package(pipeline=graph, checkpoints=checkpoints, output_base_dir=tmp_path/'exports', package_name=family)['package_path'])
    output = tmp_path / 'result.json'
    run = subprocess.run([sys.executable, str(package/'run_flow.py'), '--image', str(image), '--output', str(output)],
                         cwd=tmp_path, env={**os.environ, 'PYTHONPATH': ''}, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr
    deployed = json.loads(output.read_text())
    assert compare_flow_results(reference, deployed)['status'] == 'passed'
    assert deployed['crops'][0].get('ocr_regions') == row.get('ocr_regions')
    assert deployed['crops'][0].get('polygon') == row.get('polygon')
