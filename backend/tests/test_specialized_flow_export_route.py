import json
from pathlib import Path
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.ocr import write_ocr_manifest, train_ocr
from backend.engine.flowchart_engine import get_single_segmentation_flowchart


def test_saved_ocr_flow_exports_real_specialized_checkpoint(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Text flow', 'task': 'segmentation'}).json()
    source = tmp_path / 'source'; source.mkdir()
    for i in range(3):
        pixels = np.full((16, 48, 3), 50 + 70 * i, np.uint8)
        Image.fromarray(pixels).save(source / f'{i}.png')
    write_ocr_manifest(source, [{'image': f'{i}.png', 'text': 'A', 'split': split} for i, split in enumerate(('train', 'val', 'test'))])
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    job = '1' * 32
    train_ocr(source, Path(project['models_dir']) / 'ocr' / job, epochs=1, image_size=(16, 48))
    pipeline = get_single_segmentation_flowchart(job)
    model = next(node for node in pipeline.nodes if node.data.node_type == 'inspection')
    model.data.task = 'ocr'; model.data.params = {'expected_text': 'A'}
    saved = client.post('/api/flowchart/pipeline', params={'recipe_task': 'ocr', 'source_dataset_path': str(source)}, json=pipeline.model_dump())
    assert saved.status_code == 200, saved.text
    exported = client.post('/api/export/flow', json={'source_dataset_path': str(source), 'recipe_task': 'ocr', 'package_name': 'text_pipeline'})
    assert exported.status_code == 200, exported.text
    manifest = json.loads((Path(exported.json()['package_path']) / 'manifest.json').read_text())
    assert manifest['models'][0]['task'] == 'ocr'
    assert manifest['models'][0]['job_id'] == job
