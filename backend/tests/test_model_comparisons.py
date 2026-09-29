"""A comparison is saved evidence for one project and one exact test sample."""

import hashlib
import json
from pathlib import Path

import torch
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_dataset
from backend.api.routes_model_comparisons import _summary
from backend.engine.classification.model import create_classification_model
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(project_dir=str(tmp_path / "workspace_registry"))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _checkpoint(job_dir: Path, source: Path, fingerprint: str, preferred_class: int) -> None:
    (job_dir / "dataset").mkdir(parents=True)
    model = create_classification_model("resnet18", 2, pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.fc.bias[preferred_class] = 10.0
    torch.save({
        "task": "classification", "backbone": "resnet18", "classes": ["OK", "NG"],
        "image_size": [64, 64], "model_state_dict": model.state_dict(),
    }, job_dir / "best_model.pt")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "classification", "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint, "dataset_path": str(job_dir / "dataset"),
    }), encoding="utf-8")


def test_real_test_image_comparison_persists_hashes_disagreements_and_project_scope(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(tmp_path)
    source = tmp_path / "source"
    for category, color in (("OK", "white"), ("NG", "black")):
        folder = source / "test" / category
        folder.mkdir(parents=True)
        Image.new("RGB", (64, 64), color).save(folder / f"{category.lower()}.png")
    project = client.post("/api/project/create", json={"name": "Comparison", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    models = Path(project["models_dir"])
    _checkpoint(models / "job_incumbent", source, fingerprint, preferred_class=0)
    _checkpoint(models / "job_candidate", source, fingerprint, preferred_class=1)
    # Compare an older trained pair against the current fixed test set. The
    # report records both training provenance and the new evaluation version.
    (source / "updated_labels.txt").write_text("reviewed after training", encoding="utf-8")
    current_fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    params = {"source_dataset_path": str(source), "task": "classification"}

    catalog = client.get("/api/evaluation/model-comparisons/models", params=params)
    assert catalog.status_code == 200, catalog.text
    assert {model["job_id"] for model in catalog.json()["models"]} == {"job_incumbent", "job_candidate"}
    created = client.post("/api/evaluation/model-comparisons", json={
        **params, "incumbent_job_id": "job_incumbent", "candidate_job_id": "job_candidate", "max_images": 2,
    })
    assert created.status_code == 200, created.text
    report = created.json()
    assert report["status"] == "completed"
    assert report["summary"]["selected_images"] == 2
    assert report["summary"]["comparable_images"] == 2
    assert report["summary"]["disagreements"] == 2
    assert report["summary"]["known_ok_images"] == 1
    assert report["summary"]["known_ng_images"] == 1
    assert report["summary"]["new_missed_ng"] == 0
    assert report["summary"]["new_overkill_ok"] == 1
    assert report["model_sha256"]["incumbent"] == hashlib.sha256(
        (models / "job_incumbent" / "best_model.pt").read_bytes()).hexdigest()
    assert report["dataset_fingerprint"] == current_fingerprint
    assert report["dataset_fingerprint"] != fingerprint
    assert report["incumbent_training_dataset_fingerprint"] == fingerprint
    assert {row["incumbent"]["verdict"] for row in report["images"]} == {"OK"}
    assert {row["candidate"]["verdict"] for row in report["images"]} == {"NG"}
    for row in report["images"]:
        assert row["disagrees"] is True
        assert row["image_sha256"] == hashlib.sha256(Path(row["file_path"]).read_bytes()).hexdigest()
    report_file = Path(project["reports_dir"]) / "model_comparisons" / f"{report['comparison_id']}.json"
    assert report_file.is_file()
    assert client.get(f"/api/evaluation/model-comparisons/{report['comparison_id']}", params=params).json() == report
    assert client.get("/api/evaluation/model-comparisons", params=params).json()["total"] == 1

    # Reading another project never recovers this project's models or report.
    second = client.post("/api/project/create", json={"name": "Other", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert client.get("/api/evaluation/model-comparisons/models", params=params).json()["models"] == []
    assert client.get("/api/evaluation/model-comparisons", params=params).json()["comparisons"] == []
    assert client.get(f"/api/evaluation/model-comparisons/{report['comparison_id']}", params=params).status_code == 404
    assert second["id"] != project["id"]


def test_comparison_requires_distinct_completed_models_and_test_images(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    project = client.post("/api/project/create", json={"name": "Comparison", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    payload = {"source_dataset_path": str(source), "task": "classification",
               "incumbent_job_id": "job_a", "candidate_job_id": "job_b"}
    assert client.post("/api/evaluation/model-comparisons", json={**payload, "candidate_job_id": "job_a"}).status_code == 422
    assert client.post("/api/evaluation/model-comparisons", json=payload).status_code == 409
    assert client.get("/api/evaluation/model-comparisons/models", params={
        "source_dataset_path": str(tmp_path / "other"), "task": "classification"}).status_code == 409
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    for job_id in ("job_a", "job_b"):
        job_dir = Path(project["models_dir"]) / job_id
        job_dir.mkdir()
        (job_dir / "best_model.pt").write_bytes(b"checkpoint")
        (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
        (job_dir / "job_receipt.json").write_text(json.dumps({
            "status": "completed", "task": "classification", "source_dataset_path": str(source),
            "dataset_fingerprint": fingerprint,
        }), encoding="utf-8")
    missing_test = client.post("/api/evaluation/model-comparisons", json=payload)
    assert missing_test.status_code == 422
    assert "test" in missing_test.json()["detail"]


def test_known_ng_to_candidate_ok_is_reported_as_possible_new_miss():
    summary = _summary([{
        "ground_truth_verdict": "NG",
        "incumbent": {"verdict": "NG"},
        "candidate": {"verdict": "OK"},
    }])
    assert summary["new_missed_ng"] == 1
    assert summary["new_overkill_ok"] == 0
    assert summary["disagreements"] == 1
