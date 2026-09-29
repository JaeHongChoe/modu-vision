"""Patch classification keeps image provenance from labels through inference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
import torch.nn as nn
from PIL import Image
from fastapi import HTTPException

from backend.engine.patch_classification import (
    PatchClassificationDataset,
    load_patch_manifest,
    predict_patch_classification,
)


def _image(root: Path, name: str, value: int) -> str:
    path = root / "images" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.zeros((32, 32, 3), dtype=np.uint8)
    pixels[:, :, 0] = value
    Image.fromarray(pixels).save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(root: Path) -> dict:
    images = [
        ("train_ok.png", 15, "OK", "train"),
        ("train_ng.png", 230, "NG", "train"),
        ("val_ok.png", 30, "OK", "val"),
        ("val_ng.png", 220, "NG", "val"),
        ("test_ok.png", 45, "OK", "test"),
        ("test_ng.png", 210, "NG", "test"),
    ]
    patches = []
    for name, value, label, split in images:
        digest = _image(root, name, value)
        patches.append({
            "image": f"images/{name}", "box": [0, 0, 16, 16],
            "label": label, "split": split, "source_sha256": digest,
        })
    data = {
        "version": 1, "classes": ["OK", "NG"], "normal_class": "OK",
        "patch_size": 16, "stride": 16, "patches": patches,
    }
    (root / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def test_dataset_crops_patch_and_records_source_provenance(tmp_path: Path):
    _manifest(tmp_path)
    manifest = load_patch_manifest(tmp_path)
    train = PatchClassificationDataset(tmp_path, split="train", image_size=(16, 16), manifest=manifest)
    test = PatchClassificationDataset(tmp_path, split="test", image_size=(16, 16), manifest=manifest)

    assert train.classes == ["OK", "NG"]
    assert train.class_counts == [1, 1]
    assert len(train) == 2 and len(test) == 2
    tensor, label = train[0]
    assert tensor.shape == (3, 16, 16)
    assert label == 0
    assert float(tensor[0].mean()) == pytest.approx(15 / 255)
    assert manifest.provenance["source_sha256"]["images/train_ok.png"] == train.samples[0].source_sha256
    assert manifest.provenance["dataset_sha256"].startswith("sha256:")


def test_manifest_rejects_cross_split_source_and_changed_bytes(tmp_path: Path):
    data = _manifest(tmp_path)
    data["patches"][2]["image"] = data["patches"][0]["image"]
    data["patches"][2]["source_sha256"] = data["patches"][0]["source_sha256"]
    (tmp_path / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="split"):
        load_patch_manifest(tmp_path)

    data = _manifest(tmp_path)
    _image(tmp_path, "train_ok.png", 99)
    with pytest.raises(ValueError, match="SHA-256"):
        load_patch_manifest(tmp_path)


def test_dataset_rejects_source_changed_after_manifest_was_loaded(tmp_path: Path):
    _manifest(tmp_path)
    manifest = load_patch_manifest(tmp_path)
    train = PatchClassificationDataset(tmp_path, split="train", image_size=(16, 16), manifest=manifest)
    _image(tmp_path, "train_ok.png", 99)

    with pytest.raises(ValueError, match="changed after patch manifest validation"):
        train[0]


def test_manifest_rejects_path_escape_and_outside_box(tmp_path: Path):
    data = _manifest(tmp_path)
    data["patches"][0]["image"] = "../outside.png"
    (tmp_path / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="inside"):
        load_patch_manifest(tmp_path)

    data = _manifest(tmp_path)
    data["patches"][0]["box"] = [0, 0, 64, 64]
    (tmp_path / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="bounds"):
        load_patch_manifest(tmp_path)


class _RedMeanClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.scale = nn.Parameter(torch.tensor(10.0))

    def forward(self, images):
        red = images[:, 0].mean(dim=(1, 2))
        return torch.stack(((0.5 - red) * self.scale, (red - 0.5) * self.scale), dim=1)


def test_predict_returns_patch_boxes_scores_and_model_source_hashes(tmp_path: Path, monkeypatch):
    _manifest(tmp_path)
    from backend.engine import patch_classification as patch_module

    monkeypatch.setattr(patch_module, "create_classification_model", lambda **_: _RedMeanClassifier())
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    model_file = model_dir / "best_model.pt"
    torch.save({"model_state_dict": _RedMeanClassifier().state_dict()}, model_file)
    (model_dir / "model_meta.json").write_text(json.dumps({
        "task": "patch_classification", "classes": ["OK", "NG"], "normal_class": "OK",
        "backbone": "resnet18", "patch_size": 16, "stride": 16, "image_size": [16, 16],
    }), encoding="utf-8")
    sample = tmp_path / "images" / "test_ng.png"

    result = predict_patch_classification(model_file, sample, device="cpu", threshold=0.5)

    assert result["task"] == "patch_classification"
    assert result["decision"] == "FAIL"
    assert result["source_image"] == str(sample.resolve())
    assert result["source_sha256"] == hashlib.sha256(sample.read_bytes()).hexdigest()
    assert result["model_sha256"] == hashlib.sha256(model_file.read_bytes()).hexdigest()
    assert [item["box"] for item in result["patches"]] == [
        [0, 0, 16, 16], [16, 0, 32, 16], [0, 16, 16, 32], [16, 16, 32, 32],
    ]
    assert all(item["predicted_class"] == "NG" and item["defect_score"] > 0.5 for item in result["patches"])


def test_predict_rejects_wrong_checkpoint_task(tmp_path: Path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    torch.save({"model_state_dict": {}}, model_dir / "best_model.pt")
    (model_dir / "model_meta.json").write_text('{"task":"classification"}', encoding="utf-8")
    with pytest.raises(ValueError, match="patch_classification"):
        predict_patch_classification(model_dir / "best_model.pt", np.zeros((16, 16, 3), dtype=np.uint8))


def test_train_and_evaluate_test_split_with_provenance(tmp_path: Path, monkeypatch):
    _manifest(tmp_path)
    from backend.engine import trainer as trainer_module
    from backend.api import routes_evaluation as evaluation_module

    monkeypatch.setattr(trainer_module, "create_classification_model", lambda **_: _RedMeanClassifier())
    monkeypatch.setattr(evaluation_module, "create_classification_model", lambda **_: _RedMeanClassifier())
    job_dir = tmp_path / "model"
    trainer = trainer_module.UnifiedAutoMLTrainer(
        task="patch_classification", dataset_path=tmp_path, output_dir=job_dir,
        device="cpu", config_overrides={"epochs": 1, "batch_size": 2, "image_size": 64},
    )
    result = trainer.train(job_id="job_patch_test")
    assert result["status"] == "completed"
    meta = json.loads((job_dir / "model_meta.json").read_text(encoding="utf-8"))
    assert meta["task"] == "patch_classification"
    assert meta["patch_size"] == 16 and meta["stride"] == 16
    assert meta["normal_class"] == "OK"
    assert meta["patch_provenance"]["source_image_count"] == 6

    evaluated = evaluation_module._evaluate_patch_classification(
        job_dir / "best_model.pt", meta, tmp_path, torch.device("cpu"),
    )
    assert len(evaluated["test_predictions"]) == 2
    assert {row["file_name"] for row in evaluated["test_predictions"]} == {"test_ok.png", "test_ng.png"}
    assert all(row["box"] == [0, 0, 16, 16] for row in evaluated["test_predictions"])
    assert all(len(row["source_sha256"]) == 64 for row in evaluated["test_predictions"])
    assert evaluated["dataset_provenance"]["dataset_sha256"] == meta["patch_provenance"]["dataset_sha256"]

    _image(tmp_path, "test_ng.png", 180)
    with pytest.raises(HTTPException) as changed:
        evaluation_module._evaluate_patch_classification(
            job_dir / "best_model.pt", meta, tmp_path, torch.device("cpu"),
        )
    assert changed.value.status_code == 409


def test_training_route_rejects_patch_dataset_without_validation_split(tmp_path: Path):
    from backend.api.routes_training import TrainingStartRequest, start_training

    data = _manifest(tmp_path)
    data["patches"] = [row for row in data["patches"] if row["split"] != "val"]
    (tmp_path / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(HTTPException) as invalid:
        start_training(TrainingStartRequest(task="patch_classification", dataset_path=str(tmp_path)))
    assert invalid.value.status_code == 422


def test_patch_holdout_requires_explicit_image_truth_and_keeps_source_hashes(tmp_path: Path):
    from backend.api.routes_model_comparisons import _test_images

    data = _manifest(tmp_path)
    with pytest.raises(HTTPException, match="test_image_verdicts"):
        _test_images(tmp_path, "patch_classification", maximum=16)
    data["test_image_verdicts"] = {
        "images/test_ok.png": "OK",
        "images/test_ng.png": "NG",
    }
    (tmp_path / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    rows, total = _test_images(tmp_path, "patch_classification", maximum=16)
    assert total == 2
    assert [(row["file_name"], row["ground_truth_verdict"]) for row in rows] == [
        ("test_ng.png", "NG"), ("test_ok.png", "OK"),
    ]
    assert all(row["image_sha256"] == hashlib.sha256(Path(row["file_path"]).read_bytes()).hexdigest() for row in rows)


def test_patch_comparison_catalog_accepts_segmentation_project_source(tmp_path: Path):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    source = tmp_path / "source"
    source.mkdir()
    assert client.post("/api/project/create", json={"name": "Mixed", "task": "segmentation"}).status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    catalog = client.get("/api/evaluation/model-comparisons/models", params={
        "source_dataset_path": str(source), "task": "patch_classification",
    })
    assert catalog.status_code == 200, catalog.text
    assert catalog.json() == {"models": [], "total": 0}


def test_patch_holdout_selection_balances_ok_ng_when_more_than_limit(tmp_path: Path):
    from backend.api.routes_model_comparisons import _test_images

    data = _manifest(tmp_path)
    truth = {"images/test_ok.png": "OK", "images/test_ng.png": "NG"}
    for index in range(9):
        for label, prefix, color in (("NG", "a", 180 + index), ("OK", "z", 60 + index)):
            name = f"{prefix}_{index:02d}.png"
            digest = _image(tmp_path, name, color)
            relative = f"images/{name}"
            data["patches"].append({
                "image": relative, "box": [0, 0, 16, 16], "label": label,
                "split": "test", "source_sha256": digest,
            })
            truth[relative] = label
    data["test_image_verdicts"] = truth
    (tmp_path / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    selected, total = _test_images(tmp_path, "patch_classification", maximum=16)
    assert total == 20
    assert len(selected) == 16
    assert sum(row["ground_truth_verdict"] == "OK" for row in selected) == 8
    assert sum(row["ground_truth_verdict"] == "NG" for row in selected) == 8


def test_patch_models_run_same_test_sources_then_require_holdout_gate(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.api import routes_dataset
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine import patch_classification as patch_module

    source = tmp_path / "source"
    data = _manifest(source)
    data["test_image_verdicts"] = {
        "images/test_ok.png": "OK", "images/test_ng.png": "NG",
    }
    (source / "patches.json").write_text(json.dumps(data), encoding="utf-8")
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.post("/api/project/create", json={"name": "Mixed QA", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    source_fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    monkeypatch.setattr(patch_module, "create_classification_model", lambda **_: _RedMeanClassifier())
    for job_id, scale in (("job_patch_base", 10.0), ("job_patch_candidate", 12.0)):
        directory = Path(project["models_dir"]) / job_id
        directory.mkdir()
        model = _RedMeanClassifier()
        with torch.no_grad():
            model.scale.fill_(scale)
        torch.save({"model_state_dict": model.state_dict()}, directory / "best_model.pt")
        (directory / "model_meta.json").write_text(json.dumps({
            "task": "patch_classification", "classes": ["OK", "NG"], "normal_class": "OK",
            "backbone": "resnet18", "patch_size": 16, "stride": 16, "image_size": [64, 64],
        }))
        (directory / "job_receipt.json").write_text(json.dumps({
            "job_id": job_id, "status": "completed", "task": "patch_classification",
            "source_dataset_path": str(source), "dataset_fingerprint": source_fingerprint,
        }))
    compared = client.post("/api/evaluation/model-comparisons", json={
        "source_dataset_path": str(source), "task": "patch_classification",
        "incumbent_job_id": "job_patch_base", "candidate_job_id": "job_patch_candidate",
        "max_images": 2,
    })
    assert compared.status_code == 200, compared.text
    report = compared.json()
    assert report["status"] == "completed"
    assert report["summary"]["known_ok_images"] == 1
    assert report["summary"]["known_ng_images"] == 1
    assert {row["ground_truth_verdict"] for row in report["images"]} == {"OK", "NG"}
    assessed = client.get(f"/api/model-deployments/assess/{report['comparison_id']}", params={
        "source_dataset_path": str(source), "task": "patch_classification",
    })
    assert assessed.status_code == 200, assessed.text
    assert assessed.json()["status"] == "needs_review"
    assert assessed.json()["minimum_each_class"] == 8
