"""A completed model's saved task survives a genuine project editor task switch.

These controls freeze real owned pixels and LabelMe truth through production
cohort code. They do not train/load a model or invent a completed evaluation.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _files(root):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, _sha(p))
            for p in root.rglob('*') if p.is_file()}


@contextmanager
def _scope(project):
    from backend.engine.annotation_storage import (
        set_request_annotation_root, reset_request_annotation_root,
        set_request_project_root, reset_request_project_root,
    )
    from backend.engine.dataset_loaders import set_request_split_root, reset_request_split_root
    project_token = set_request_project_root(Path(project['project_dir']))
    annotation_token = set_request_annotation_root(Path(project['annotations_dir']))
    split_token = set_request_split_root(Path(project['dataset_dir']) / 'splits')
    try:
        yield
    finally:
        reset_request_split_root(split_token)
        reset_request_annotation_root(annotation_token)
        reset_request_project_root(project_token)


def _workspace(tmp_path, monkeypatch):
    from backend.tests.test_model_comparisons import _client
    from backend.api import routes_dataset
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    source = tmp_path / 'source'
    shapes = [
        {'label': 'scratch', 'shape_type': 'rectangle', 'points': [[20, 12], [25, 20]]},
        {'label': 'stain', 'shape_type': 'polygon',
         'points': [[70, 30], [103, 33], [95, 69], [68, 58]]},
    ]
    for i, partition in enumerate(('train', 'val', 'test')):
        for j, label in enumerate(('background', 'defect')):
            image = source / partition / (label + '.png')
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (128, 96), (30 + 40 * i, 50 + 20 * j, 20)).save(image)
            image.with_suffix('.json').write_text(json.dumps({
                'version': '5.0', 'imagePath': image.name, 'imageWidth': 128,
                'imageHeight': 96, 'imageData': None, 'flags': {},
                'shapes': shapes if label == 'defect' else [],
            }))
    client = _client(tmp_path)
    response = client.post('/api/project/create', json={'name': 'Two task owned cohort', 'task': 'detection'})
    assert response.status_code == 200, response.text
    response = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert response.status_code == 200, response.text
    project = response.json()
    with _scope(project):
        routes_dataset._write_split_manifest(source, {
            p.relative_to(source).as_posix(): p.relative_to(source).parts[0]
            for p in source.rglob('*.png')
        }, 114)
    response = client.post('/api/dataset/versions', json={'name': 'Saved detector truth'})
    assert response.status_code == 200, response.text
    version = response.json()
    meta = {'task': 'detection', 'classes': ['scratch', 'stain'],
            'training_provenance': {'labelset_id': 'default'}}
    selection = {_sha(p) for p in source.rglob('*.png')
                 if p.relative_to(source).parts[0] in ('train', 'val')}
    return client, project, source, version, meta, selection


@pytest.mark.parametrize('editor_task', ['classification', 'detection', 'segmentation', 'anomaly'])
@pytest.mark.parametrize('context_kind', ['local_selection', 'remote_task'])
def test_saved_detector_truth_survives_real_project_editor_task_switch(tmp_path, monkeypatch, editor_task, context_kind):
    from backend.remote.evaluation_cohort import freeze_cohort
    client, _, source, version, meta, selection = _workspace(tmp_path, monkeypatch)
    response = client.put('/api/project/update', json={'task': editor_task})
    assert response.status_code == 200, response.text
    project = response.json()
    assert project['task'] == editor_task
    context = SimpleNamespace(selection_sha256=selection)
    if context_kind == 'remote_task':
        context.task = 'detection'
    before_project = (Path(project['project_dir']) / 'project.json').read_bytes()
    before_pixels = _files(source)
    before = copy.deepcopy(project)
    with _scope(project):
        cohort = freeze_cohort(project, version['id'], source, 'detection', meta, context)
    assert project == before and cohort['project'] == before
    assert (Path(project['project_dir']) / 'project.json').read_bytes() == before_project
    assert _files(source) == before_pixels
    assert cohort['project_id'] == project['id'] and cohort['labelset_id'] == 'default'
    descriptor = cohort['descriptor']
    assert descriptor['task'] == 'detection' and descriptor['split'] == 'test'
    assert descriptor['classes'] == ['scratch', 'stain']
    assert cohort['image_count'] == 2
    samples = {row['source_relative_path']: row for row in descriptor['ordered_samples']}
    assert set(samples) == {'test/background.png', 'test/defect.png'}
    for relative, row in samples.items():
        assert row['sha256'] == _sha(source / relative)
        assert (row['width'], row['height']) == (128, 96)
    assert samples['test/background.png']['truth']['objects'] == []
    assert samples['test/defect.png']['truth']['objects'] == [
        {'label': 'scratch', 'box': [20.0, 12.0, 25.0, 20.0]},
        {'label': 'stain', 'box': [68.0, 30.0, 103.0, 69.0]},
    ]
    assert cohort['archive'].is_file()
    assert _sha(cohort['archive']) == cohort['archive_sha256']


@pytest.mark.parametrize('mismatch', ['metadata', 'remote_context', 'version', 'unsupported_editor'])
def test_saved_task_mismatch_refuses_before_freezing_outputs(tmp_path, monkeypatch, mismatch):
    from backend.remote.evaluation_cohort import freeze_cohort
    client, project, source, version, meta, selection = _workspace(tmp_path, monkeypatch)
    response = client.put('/api/project/update', json={'task': 'segmentation'})
    assert response.status_code == 200, response.text
    project = response.json()
    context = SimpleNamespace(selection_sha256=selection, task='detection')
    if mismatch == 'metadata':
        meta = {**meta, 'task': 'segmentation'}
    elif mismatch == 'remote_context':
        context.task = 'segmentation'
    elif mismatch == 'version':
        response = client.post('/api/dataset/versions', json={'name': 'Different task version'})
        assert response.status_code == 200, response.text
        version = response.json()
    else:
        project = {**project, 'task': 'foreign'}
    before = copy.deepcopy(project)
    project_bytes = (Path(project['project_dir']) / 'project.json').read_bytes()
    pixels = _files(source)
    output = Path(project['reports_dir']) / 'evaluation_cohorts'
    assert not output.exists()
    with _scope(project), pytest.raises(HTTPException) as error:
        freeze_cohort(project, version['id'], source, 'detection', meta, context)
    assert error.value.status_code == 422
    assert 'task differs' in error.value.detail
    assert not output.exists()
    assert project == before and _files(source) == pixels
    assert (Path(project['project_dir']) / 'project.json').read_bytes() == project_bytes
