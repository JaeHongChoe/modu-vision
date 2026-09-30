from pathlib import Path

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


def test_project_scoped_gan_manifest_train_and_review_candidates(tmp_path: Path):
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.post("/api/project/create", json={"name": "GAN QA", "task": "segmentation"})
    assert project.status_code == 200, project.text

    source = tmp_path / "gan_crops"
    source.mkdir()
    client.put("/api/project/update",json={"source_dataset_dir":str(source)})
    rows = []
    for index in range(2):
        array = np.full((64, 64, 3), 30 + index * 80, dtype=np.uint8)
        image = source / f"sample_{index}.png"
        Image.fromarray(array).save(image)
        rows.append({"image": image.name, "bbox": [0, 0, 64, 64], "split": "train"})

    created = client.post("/api/defect-gan/manifest", json={"dataset_path": str(source), "samples": rows})
    assert created.status_code == 200, created.text
    assert created.json()["sample_count"] == 2
    inspected = client.get("/api/defect-gan/manifest", params={"dataset_path": str(source)})
    assert inspected.status_code == 200, inspected.text
    assert [row["image"] for row in inspected.json()["samples"]] == ["sample_0.png", "sample_1.png"]
    trained = client.post("/api/defect-gan/train", json={
        "dataset_path": str(source), "epochs": 1, "batch_size": 2, "base_channels": 8,
    })
    assert trained.status_code == 200, trained.text
    job_id = trained.json()["job_id"]
    assert client.get("/api/defect-gan/models").json()["models"][0]["job_id"] == job_id
    generated = client.post("/api/defect-gan/generate", json={"job_id": job_id, "count": 2, "seed": 7})
    assert generated.status_code == 200, generated.text
    assert [item["status"] for item in generated.json()["candidates"]] == ["synthetic_unreviewed"] * 2
    assert all(item["preview_data_url"].startswith("data:image/png;base64,") for item in generated.json()["candidates"])

    other = client.post("/api/project/create", json={"name": "Other GAN QA", "task": "segmentation"})
    assert other.status_code == 200
    denied = client.post("/api/defect-gan/generate", json={"job_id": job_id, "count": 1})
    assert denied.status_code == 404


def test_gan_diagnostic_export_and_review_reopen(tmp_path:Path):
    app=create_app(project_dir=str(tmp_path/'workspace'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    client.post('/api/project/create',json={'name':'GAN handoff','task':'classification'})
    source=tmp_path/'source';source.mkdir();rows=[]
    for index,split in enumerate(('train','train','val','test')):
        path=source/f'{index}.png';Image.fromarray(np.full((64,64,3),30+index*40,np.uint8)).save(path)
        rows.append({'image':path.name,'bbox':[0,0,64,64],'split':split})
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    client.post('/api/defect-gan/manifest',json={'dataset_path':str(source),'samples':rows})
    trained=client.post('/api/defect-gan/train',json={'dataset_path':str(source),'epochs':1,'batch_size':2,'base_channels':8});assert trained.status_code==200,trained.text
    job=trained.json()['job_id']
    evaluated=client.post('/api/defect-gan/evaluate',json={'job_id':job,'dataset_path':str(source)})
    assert evaluated.status_code==200 and evaluated.json()['quality_status']=='unvalidated'
    assert evaluated.json()["evaluation_id"]
    assert evaluated.json()["binding"]["checkpoint_sha256"]
    exported=client.post('/api/defect-gan/export',json={'job_id':job})
    assert exported.status_code==200,exported.text
    assert Path(exported.json()['package_path']).joinpath('generate.py').is_file()
    generated=client.post('/api/defect-gan/generate',json={'job_id':job,'count':1});assert generated.status_code==200
    reviews=client.get('/api/defect-gan/reviews').json()['reviews'];assert len(reviews)==1
    reopened=client.get(f'/api/defect-gan/reviews/{job}/{reviews[0]["review_id"]}')
    assert reopened.status_code==200,reopened.text
    assert reopened.json()['candidates'][0]['sha256']==generated.json()['candidates'][0]['sha256']
    client.post('/api/project/create',json={'name':'Other project','task':'classification'})
    assert client.get('/api/defect-gan/reviews').json()=={'reviews':[]}
    assert client.get(f'/api/defect-gan/reviews/{job}/{reviews[0]["review_id"]}').status_code==404
