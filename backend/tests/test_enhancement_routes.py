from pathlib import Path
import time

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


def test_enhancement_preparation_training_evaluation_are_project_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_API_TOKEN", "enhancement-test-session")
    client = TestClient(create_app(project_dir=str(tmp_path / "workspace")))
    client.headers["x-vision-token"] = "enhancement-test-session"
    project = client.post("/api/project/create", json={"name": "Image improvement", "task": "segmentation"}).json()
    source = tmp_path / "source"
    source.mkdir()
    for index in range(5):
        Image.fromarray(np.full((24, 32, 3), index * 40 + 30, np.uint8)).save(source / f"{index}.png")
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    prepared = client.post("/api/enhancement/prepare", json={"source_dataset_path": str(source)})
    assert prepared.status_code == 200, prepared.text
    dataset = prepared.json()["dataset_path"]
    trained = client.post("/api/enhancement/train", json={"dataset_path": dataset, "epochs": 1})
    assert trained.status_code == 200, trained.text
    job = trained.json()["job_id"]
    models = client.get("/api/enhancement/models").json()["models"]
    assert [m["job_id"] for m in models] == [job]
    evaluated = client.post("/api/enhancement/evaluate", json={"job_id": job, "dataset_path": dataset})
    assert evaluated.status_code == 200 and evaluated.json()["sample_count"] == 1
    assert Path(project["models_dir"]).joinpath("enhancement", job, "best_model.pt").is_file()
    client.post("/api/project/create", json={"name": "Another project", "task": "segmentation"})
    assert client.get("/api/enhancement/models").json()["models"] == []
    assert client.post("/api/enhancement/evaluate", json={"job_id": job, "dataset_path": dataset}).status_code == 404


def test_background_enhancement_can_be_cancelled_and_reopened(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.post('/api/project/create', json={'name': 'Cancelable improvement', 'task': 'segmentation'})
    source = tmp_path / 'source'; source.mkdir()
    for i in range(4):
        Image.fromarray(np.full((40, 48, 3), 30 + i * 40, np.uint8)).save(source / f'{i}.png')
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    dataset = client.post('/api/enhancement/prepare', json={'source_dataset_path': str(source)}).json()['dataset_path']
    queued = client.post('/api/enhancement/train', json={'dataset_path': dataset, 'epochs': 500, 'background': True})
    assert queued.status_code == 202, queued.text
    job = queued.json()['job_id']
    cancelled = client.post(f'/api/enhancement/jobs/{job}/cancel')
    assert cancelled.status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f'/api/enhancement/jobs/{job}').json()
        if status['status'] not in {'queued', 'running', 'stopping'}: break
        time.sleep(.02)
    assert status['status'] == 'stopped'
    assert client.get('/api/enhancement/jobs').json()['jobs'][0]['job_id'] == job


def test_failed_enhancement_checkpoint_cannot_evaluate_or_predict(tmp_path):
    """A candidate may have weights before final provenance validation fails."""
    import json
    import torch
    from backend.engine.enhancement import RGBDenoiser

    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Failed candidate', 'task': 'segmentation'}).json()
    source = tmp_path / 'source'; source.mkdir()
    image = source / 'part.png'
    Image.fromarray(np.full((16, 20, 3), 80, np.uint8)).save(image)
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    job_id = 'f' * 32
    folder = Path(project['models_dir']) / 'enhancement' / job_id
    folder.mkdir(parents=True)
    torch.save({'task': 'enhancement', 'version': 1, 'model_state_dict': RGBDenoiser().state_dict()}, folder / 'best_model.pt')
    (folder / 'model_meta.json').write_text(json.dumps({'task': 'enhancement'}))
    (folder / 'job.json').write_text(json.dumps({'job_id': job_id, 'status': 'failed', 'created_at': 0,
                                               'error': 'Post-training provenance validation failed'}))
    assert client.get('/api/enhancement/models').json()['models'] == []
    # Reject the failed journal before processing an evaluation manifest or image.
    evaluated = client.post('/api/enhancement/evaluate', json={'job_id': job_id, 'dataset_path': str(tmp_path / 'pairs')})
    predicted = client.post('/api/enhancement/predict', json={'job_id': job_id, 'image_path': str(image)})
    assert evaluated.status_code == 409, evaluated.text
    assert predicted.status_code == 409, predicted.text


def test_cancel_during_enhancement_final_binding_does_not_complete(tmp_path, monkeypatch):
    """Cancellation is accepted while the checkpoint's provenance is persisted."""
    from backend.engine import training_provenance
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name':'Finalizing cancellation','task':'segmentation'}).json()
    source = tmp_path/'source'; source.mkdir()
    for index in range(3):
        Image.fromarray(np.full((24,32,3),40+index*65,np.uint8)).save(source/f'{index}.png')
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    prepared = client.post('/api/enhancement/prepare',json={'source_dataset_path':str(source)})
    assert prepared.status_code==200,prepared.text
    cancellations = []
    def cancel_during_binding(output, binding):
        response = client.post(f'/api/enhancement/jobs/{Path(output).name}/cancel')
        assert response.status_code==200,response.text
        cancellations.append(response.json())
    monkeypatch.setattr(training_provenance,'persist_model_binding',cancel_during_binding)
    trained = client.post('/api/enhancement/train',json={'dataset_path':prepared.json()['dataset_path'],'epochs':1})
    assert [row['status'] for row in cancellations]==['stopping']
    assert trained.status_code==409,trained.text
    job_id=cancellations[0]['job_id']
    assert client.get(f'/api/enhancement/jobs/{job_id}').json()['status']=='stopped'
    assert client.get('/api/enhancement/models').json()['models']==[]
    # A checkpoint written before finalization must still be inaccessible.
    assert client.post('/api/enhancement/predict',json={'job_id':job_id,'image_path':str(source/'0.png')}).status_code in (404,409)
