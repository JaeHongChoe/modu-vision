"""A saved flow scores a classification image exactly as step-4 evaluation does (live finding 2026-10-21).

A model that step 4 evaluated 10/10 correct turned every OK image NG in step 6: the flow's inspection node resized
the image to the model input bicubically (INTER_AREA when shrinking), while training and evaluation resize
bilinearly, so every defect score came out 0.04-0.10 higher. The checkpoint here is built and saved the way training
saves one (random initial weights, no downloaded file), and the images are smaller and larger than its input size.
"""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch
from PIL import Image

from backend.api.routes_evaluation import _evaluate_classification
from backend.engine.class_semantics import training_class_semantics
from backend.engine.classification import create_classification_model
from backend.engine.dataset_loaders import ClassificationDataset
from backend.engine.flowchart_engine import (
    FlowEdge, FlowNode, FlowNodeData, FlowchartEngine, FlowchartPipeline, safe_crop_roi,
)

CLASSES = ['OK', 'NG']
INPUT_SIZE = (64, 48)  # (width, height); not square, so a swapped order would also show
# One image smaller than the input (upscaled), one larger (downscaled), one wider but shorter (mixed).
IMAGES = {'OK/small.png': (40, 30), 'NG/large.png': (150, 110), 'OK/mixed.png': (100, 30)}


def _write_images(root: Path) -> dict:
    rng = np.random.default_rng(7)
    paths = {}
    for relative, (width, height) in IMAGES.items():
        # Fine texture over a smooth gradient: neighbouring pixels differ, so resize methods differ.
        ramp = np.linspace(0, 160, width, dtype=np.float32)[None, :, None]
        noise = rng.integers(0, 96, size=(height, width, 3)).astype(np.float32)
        pixels = np.clip(ramp + noise, 0, 255).astype(np.uint8)
        path = root / 'test' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(pixels).save(path)
        paths[relative] = path
    return paths


def _rgb(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        return np.asarray(opened.convert('RGB'))


def _save_checkpoint(models: Path):
    """Write best_model.pt and model_meta.json with the keys training writes for a classification model."""
    torch.manual_seed(0)
    model = create_classification_model(backbone='resnet18', num_classes=len(CLASSES), pretrained=False)
    meta = {'task': 'classification', 'backbone': 'resnet18', 'model_name': 'resnet18', 'classes': CLASSES,
            'num_classes': len(CLASSES), 'image_size': list(INPUT_SIZE), 'pretrained': False,
            'class_semantics': training_class_semantics(CLASSES, task='classification')}
    folder = models / 'job_parity'
    folder.mkdir(parents=True)
    checkpoint = folder / 'best_model.pt'
    torch.save({'epoch': 1, 'model_state_dict': model.state_dict(), 'best_metric': 0.5, 'task': 'classification',
                'classes': CLASSES, 'preset': 'fast',
                **{k: v for k, v in meta.items() if k not in ('best_metric', 'task', 'classes')}}, checkpoint)
    (folder / 'model_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return checkpoint, meta


def _saved_step5_flow() -> FlowchartPipeline:
    """The step-5 single classification flow: whole image, padding 10, threshold 0.5, any defect is NG."""
    def node(node_id, node_type, **data):
        return FlowNode(id=node_id, position={'x': 0, 'y': 0}, data=FlowNodeData(
            label=node_id, node_type=node_type, threshold=0.5, crop_padding=10, rule='any_defect_is_ng', **data))
    nodes = [node('node_input', 'input'),
             node('node_inspect', 'inspection', task='classification', model_job_id='job_parity'),
             node('node_decision', 'decision'), node('node_output', 'output')]
    edges = [FlowEdge(id=f'{a}-{b}', source=a, target=b) for a, b in
             (('node_input', 'node_inspect'), ('node_inspect', 'node_decision'), ('node_decision', 'node_output'))]
    return FlowchartPipeline(id='single_classification', name='classification', nodes=nodes, edges=edges)


def test_flow_crop_is_the_pixels_training_reads(tmp_path):
    paths = _write_images(tmp_path / 'dataset')
    dataset = ClassificationDataset(root_dir=tmp_path / 'dataset', split='test', image_size=INPUT_SIZE)
    training = {Path(path).name: dataset[index][0] for index, (path, _) in enumerate(dataset.samples)}
    assert set(training) == {path.name for path in paths.values()}
    for path in paths.values():
        rgb = _rgb(path)
        height, width = rgb.shape[:2]
        # Padding 10 around the whole image clamps to the image, as in the saved flow.
        crop, bbox = safe_crop_roi(rgb, [0, 0, width, height], padding_px=10, min_size=16, target_size=INPUT_SIZE)
        assert bbox == [0, 0, width, height]
        assert crop.shape == (INPUT_SIZE[1], INPUT_SIZE[0], 3)
        flow_tensor = torch.from_numpy(crop).permute(2, 0, 1).float() / 255.0
        assert torch.equal(flow_tensor, training[path.name]), path.name


def test_flow_node_scores_each_image_as_step_4_evaluation_does(tmp_path):
    paths = _write_images(tmp_path / 'dataset')
    checkpoint, meta = _save_checkpoint(tmp_path / 'models')

    evaluation = _evaluate_classification(checkpoint, meta, tmp_path / 'dataset', torch.device('cpu'))
    evaluated = {row['file_name']: row['class_scores'] for row in evaluation['test_predictions']}
    assert set(evaluated) == {path.name for path in paths.values()}

    engine = FlowchartEngine(device='cpu', checkpoint_resolver=lambda job_id, task: checkpoint)
    flow = _saved_step5_flow()
    for path in paths.values():
        result = engine.execute(flow, image_path=str(path))
        assert result['status'] == 'success' and len(result['crops']) == 1, result.get('error_message')
        crop = result['crops'][0]
        expected = 1.0 - evaluated[path.name]['OK']  # defect score = 1 - P(normal); OK is the normal class
        assert crop['defect_score'] == pytest.approx(expected, abs=1e-6), path.name
        assert crop['verdict'] == ('NG' if expected >= 0.5 else 'OK'), path.name

    # The fixture can tell the resize methods apart: the old bicubic/area resize moved these scores by about 1e-3,
    # a thousand times the tolerance above.
    model = create_classification_model(backbone='resnet18', num_classes=len(CLASSES), pretrained=False)
    model.load_state_dict(torch.load(checkpoint, map_location='cpu', weights_only=True)['model_state_dict'])
    model.eval()
    for relative, method in (('OK/small.png', cv2.INTER_CUBIC), ('NG/large.png', cv2.INTER_AREA)):
        rgb = _rgb(paths[relative])
        other = torch.from_numpy(cv2.resize(rgb, INPUT_SIZE, interpolation=method)).permute(2, 0, 1)
        with torch.no_grad():
            p_ok = float(torch.softmax(model(other.float().unsqueeze(0) / 255.0), dim=1)[0, 0])
        assert abs(p_ok - evaluated[paths[relative].name]['OK']) > 1e-5, relative
