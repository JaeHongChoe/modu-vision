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
