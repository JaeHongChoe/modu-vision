"""The OCR HTTP surface binds checkpoints to the active project."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from backend.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(project_dir=str(tmp_path / "projects"))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _dataset(root: Path) -> list[dict[str, str]]:
    (root / "images").mkdir(parents=True)
    rows = []
    for split, marker in (("train", 15), ("val", 35)):
        for text in "AB":
            image = Image.new("L", (64, 32), 255)
            ImageDraw.Draw(image).text((8, 0), text, font=ImageFont.load_default(size=24), fill=0)
            image.putpixel((0, 0), marker + ord(text))
            path = root / "images" / f"{split}_{text}.png"
            image.save(path)
            rows.append({"image": f"images/{path.name}", "text": text, "split": split})
    return rows


def test_ocr_api_requires_labels_and_scopes_models_to_project(tmp_path: Path):
    client = _client(tmp_path)
    project_a = client.post("/api/project/create", json={"name": "OCR A"}).json()
    root = tmp_path / "labeled"
    rows = _dataset(root)

    missing = client.post("/api/ocr/train", json={"dataset_path": str(root), "epochs": 1})
    assert missing.status_code == 422
    assert "explicit" in missing.json()["detail"]

    manifest = client.post("/api/ocr/manifest", json={"dataset_path": str(root), "samples": rows})
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["provenance"]["split_counts"] == {"train": 2, "val": 2, "test": 0}
    assert manifest.json()["provenance"]["source_sha256"]["images/train_A.png"] == hashlib.sha256(
        (root / "images/train_A.png").read_bytes()
    ).hexdigest()

    trained = client.post("/api/ocr/train", json={
        "dataset_path": str(root), "epochs": 1, "batch_size": 2,
        "image_height": 32, "image_width": 24, "learning_rate": 0.003, "device": "cpu",
    })
    assert trained.status_code == 200, trained.text
    job_id = trained.json()["job_id"]
    assert (Path(project_a["models_dir"]) / "ocr" / job_id / "best_model.pt").is_file()
    assert [item["job_id"] for item in client.get("/api/ocr/models").json()["models"]] == [job_id]

    evaluated = client.post("/api/ocr/evaluate", json={
        "job_id": job_id, "dataset_path": str(root), "split": "val",
    })
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["sample_count"] == 2
    predicted = client.post("/api/ocr/predict", json={
        "job_id": job_id, "image_path": str(root / "images/val_A.png"),
    })
    assert predicted.status_code == 200, predicted.text
    assert predicted.json()["source_sha256"] == hashlib.sha256((root / "images/val_A.png").read_bytes()).hexdigest()

    client.post("/api/project/create", json={"name": "OCR B"})
    assert client.get("/api/ocr/models").json()["models"] == []
    outside = client.post("/api/ocr/predict", json={
        "job_id": job_id, "image_path": str(root / "images/val_A.png"),
    })
    assert outside.status_code == 404


def test_ocr_api_rejects_duplicate_content_across_splits(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/api/project/create", json={"name": "OCR"})
    root = tmp_path / "labeled"
    rows = _dataset(root)
    (root / "images/val_A.png").write_bytes((root / "images/train_A.png").read_bytes())

    response = client.post("/api/ocr/manifest", json={"dataset_path": str(root), "samples": rows})
    assert response.status_code == 422
    assert "split" in response.json()["detail"]
    assert not (root / "ocr.json").exists()
