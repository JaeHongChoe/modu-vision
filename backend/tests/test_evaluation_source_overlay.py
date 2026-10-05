import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_evaluation as evaluation
from backend.api import routes_evaluation_history as history_api
from backend.engine.evaluation_history import EvaluationHistory


def test_real_segmentation_loader_captures_source_hash_and_non_square_resize(tmp_path, monkeypatch):
    images, masks = tmp_path / 'images' / 'test', tmp_path / 'masks' / 'test'
    images.mkdir(parents=True); masks.mkdir(parents=True)
    source = images / 'part.png'
    Image.new('RGB', (160, 96), 'white').save(source)
    Image.fromarray(np.pad(np.ones((20, 30), dtype=np.uint8), ((8, 68), (10, 120)))).save(masks / 'part.png')
    checkpoint = tmp_path / 'model.pt'; torch.save({'model_state_dict': {}}, checkpoint)

    class ControlledForward(torch.nn.Module):
        def forward(self, images):
            return torch.zeros((len(images), 2, images.shape[2], images.shape[3]))

    monkeypatch.setattr(evaluation, 'build_segmentation_model', lambda **kw: ControlledForward())
    result = evaluation._evaluate_segmentation(checkpoint, {'image_size': [32, 32]}, tmp_path, torch.device('cpu'))
    row = result['test_predictions'][0]
    assert row['image_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row['pixel_evidence']['mapping'] == {'kind': 'full_image_resize', 'source_size': [160, 96]}
    assert row['pixel_evidence']['shape'] == [32, 32]


def _history(tmp_path, monkeypatch, captured=True):
    source = tmp_path / 'source'; source.mkdir()
    image = source / 'part.png'; Image.new('RGB', (160, 96), 'white').save(image)
    project = {'project_dir': str(tmp_path / 'project'), 'source_dataset_dir': str(source)}
    store = EvaluationHistory(Path(project['project_dir']) / 'reports' / 'evaluations')
    row = {'file_path': str(image)}
    if captured: row['image_sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
    record = store.append({'task': 'segmentation', 'test_predictions': [row]}, {'task': 'segmentation', 'source_dataset_path': str(source)})
    monkeypatch.setattr(history_api, 'get_current_project', lambda request: project)
    return source, image, record, store, SimpleNamespace()


def test_saved_source_preview_checks_exact_report_row_and_unchanged_bytes(tmp_path, monkeypatch):
    source, image, record, store, request = _history(tmp_path, monkeypatch)
    path = store.directory / (record['evaluation_id'] + '.json'); before = path.read_bytes()
    preview = history_api.evaluation_image(record['evaluation_id'], request, str(source), 'segmentation', str(image))
    assert preview['evaluation_id'] == record['evaluation_id']
    assert preview['image_sha256'] == record['result']['test_predictions'][0]['image_sha256']
    assert preview['original_size'] == [160, 96] and preview['read_only']
    assert path.read_bytes() == before


@pytest.mark.parametrize('case', ['changed_source', 'missing_hash', 'foreign_image', 'foreign_source'])
def test_saved_source_preview_refuses_unbound_or_changed_images(tmp_path, monkeypatch, case):
    source, image, record, store, request = _history(tmp_path, monkeypatch, case != 'missing_hash')
    if case == 'changed_source': Image.new('RGB', (160, 96), 'black').save(image)
    if case == 'foreign_image': image = tmp_path / 'foreign.png'; Image.new('RGB', (160, 96)).save(image)
    if case == 'foreign_source': source = tmp_path / 'foreign-source'; source.mkdir()
    with pytest.raises(HTTPException) as caught:
        history_api.evaluation_image(record['evaluation_id'], request, str(source), 'segmentation', str(image))
    assert caught.value.status_code in (404, 409)
    assert store.get(record['evaluation_id']) == record


def test_source_change_during_segmentation_loading_refuses_new_evidence(tmp_path, monkeypatch):
    images, masks = tmp_path / 'images' / 'test', tmp_path / 'masks' / 'test'
    images.mkdir(parents=True); masks.mkdir(parents=True)
    source = images / 'part.png'; Image.new('RGB', (32, 32), 'white').save(source)
    Image.new('L', (32, 32)).save(masks / 'part.png')
    checkpoint = tmp_path / 'model.pt'; torch.save({'model_state_dict': {}}, checkpoint)
    original = evaluation.SegmentationDataset.__getitem__

    def changed(dataset, index):
        value = original(dataset, index)
        Image.new('RGB', (32, 32), 'black').save(source)
        return value

    class ControlledForward(torch.nn.Module):
        def forward(self, images): return torch.zeros((len(images), 2, images.shape[2], images.shape[3]))

    monkeypatch.setattr(evaluation.SegmentationDataset, '__getitem__', changed)
    monkeypatch.setattr(evaluation, 'build_segmentation_model', lambda **kw: ControlledForward())
    with pytest.raises(HTTPException, match='changed'):
        evaluation._evaluate_segmentation(checkpoint, {'image_size': [32, 32]}, tmp_path, torch.device('cpu'))
