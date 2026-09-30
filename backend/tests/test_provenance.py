import json
from pathlib import Path

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


def test_provenance_links_image_label_version_and_rejects_other_sources(tmp_path):
    app = create_app(str(tmp_path / "workspace"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.post("/api/project/create", json={"name": "Trace", "task": "segmentation"}).json()
    source = tmp_path / "source"
    source.mkdir()
    image = source / "part.png"
    Image.fromarray(np.full((20, 25, 3), 110, np.uint8)).save(image)
    (source / "part.json").write_text(json.dumps({"imagePath": "part.png", "shapes": [{"label": "scratch", "points": [[1, 2], [7, 8]], "shape_type": "rectangle"}]}))
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    version = client.post("/api/dataset/versions", json={"name": "Reviewed baseline"}).json()
    response = client.get("/api/provenance", params={"image_path": str(image)})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["project"]["id"] == project["id"]
    assert result["image"]["image_uuid"] and len(result["image"]["content_hash"]) == 64
    assert result["label"]["sha256"]
    assert any(v["id"] == version["id"] and v["image_matches"] for v in result["dataset_versions"])
    assert result["models"] == [] and result["inspections"] == []
    assert client.get("/api/provenance", params={"image_path": str(tmp_path / "foreign.png")}).status_code == 409



def test_provenance_reports_native_yolo_label_hash_and_files(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.post('/api/project/create', json={'name': 'YOLO trace', 'task': 'detection'})
    source = tmp_path / 'source'
    (source / 'images' / 'train').mkdir(parents=True)
    (source / 'labels' / 'train').mkdir(parents=True)
    image = source / 'images' / 'train' / 'part.png'
    Image.fromarray(np.full((20, 25, 3), 110, np.uint8)).save(image)
    label = source / 'labels' / 'train' / 'part.txt'
    label.write_text('0 .5 .5 .4 .4')
    (source / 'classes.txt').write_text('scratch')
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    response = client.get('/api/provenance', params={'image_path': str(image)})
    assert response.status_code == 200, response.text
    record = response.json()
    assert len(record['label']['sha256']) == 64
    assert record['label']['sha256'] == record['image']['annotation_hash']
    assert str(label) in record['label']['paths']


def test_provenance_prefers_bound_source_and_keeps_training_identity(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Bound trace', 'task': 'classification'}).json()
    source = tmp_path / 'source'; source.mkdir()
    image = source / 'part.png'; Image.new('RGB', (12, 16)).save(image)
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    model_root = Path(project['models_dir'])
    version_root = Path(project['project_dir']) / 'versions'
    own_version = version_root / 'own'; own_version.mkdir(parents=True)
    foreign_version = version_root / 'foreign'; foreign_version.mkdir()
    (own_version / 'manifest.json').write_text(json.dumps({'source_dataset_dir': str(source)}))
    (foreign_version / 'manifest.json').write_text(json.dumps({'source_dataset_dir': str(tmp_path / 'other-source')}))
    for job, version in [('own-model', own_version), ('other-source-model', foreign_version)]:
        folder = model_root / job; folder.mkdir()
        (folder / 'best_model.pt').write_bytes(b'checkpoint evidence')
        (folder / 'model_meta.json').write_text(json.dumps({
            'task': 'classification', 'source_dataset_path': str(source),
            'dataset_path': str(tmp_path / 'prepared-view'),
            'training_provenance': {'dataset_version_id': version.name, 'version_dir': str(version),
                                    'labelset_id': 'reviewed', 'split_sha256': 'bound-split'},
        }))
    response = client.get('/api/provenance', params={'image_path': str(image)})
    assert response.status_code == 200, response.text
    models = response.json()['models']
    assert [row['job_id'] for row in models] == ['own-model']
    assert models[0]['source_dataset_path'] == str(source)
    assert models[0]['source_verified'] is True
    assert models[0]['training_provenance']['dataset_version_id'] == 'own'
    assert models[0]['training_provenance']['labelset_id'] == 'reviewed'
    assert models[0]['training_provenance']['split_sha256'] == 'bound-split'
