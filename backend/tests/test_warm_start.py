"""A retraining candidate may inherit only a verified compatible local model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch
from torch import nn
from PIL import Image

from backend.engine.warm_start import load_parent_weights, resolve_warm_start_parent


def _parent(tmp_path: Path, *, task: str = "classification") -> tuple[Path, Path, Path, nn.Module]:
    source = tmp_path / "source"
    source.mkdir()
    models = tmp_path / "project" / "models"
    job_dir = models / "job_parent01"
    job_dir.mkdir(parents=True)
    model = nn.Linear(3, 2)
    checkpoint = job_dir / "best_model.pt"
    torch.save({
        "task": task, "classes": ["OK", "NG"], "preset": "fast",
        "backbone": "resnet18", "model_state_dict": model.state_dict(),
    }, checkpoint)
    (job_dir / "model_meta.json").write_text(json.dumps({
        "task": task, "classes": ["OK", "NG"], "preset": "fast",
        "backbone": "resnet18", "created_at": "2026-09-30T00:00:00Z",
    }))
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "job_id": "job_parent01", "status": "completed", "task": task,
        "source_dataset_path": str(source), "dataset_fingerprint": "v1:old-source",
    }))
    return models, source, checkpoint, model


def test_resolved_parent_is_project_scoped_source_linked_and_hash_pinned(tmp_path: Path):
    models, source, checkpoint, _ = _parent(tmp_path)
    parent = resolve_warm_start_parent(
        "job_parent01", models, source, task="classification", architecture="classification:resnet18",
    )
    assert parent.job_id == "job_parent01"
    assert parent.checkpoint_sha256 == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert parent.dataset_fingerprint == "v1:old-source"
    assert parent.classes == ("OK", "NG")


def test_parent_rejects_wrong_source_task_architecture_or_missing_receipt(tmp_path: Path):
    models, source, checkpoint, _ = _parent(tmp_path)
    with pytest.raises(ValueError, match="source"):
        resolve_warm_start_parent("job_parent01", models, tmp_path, "classification", "classification:resnet18")
    with pytest.raises(ValueError, match="task"):
        resolve_warm_start_parent("job_parent01", models, source, "segmentation", "segmentation:fast")
    with pytest.raises(ValueError, match="architecture"):
        resolve_warm_start_parent("job_parent01", models, source, "classification", "classification:convnext_tiny")
    (checkpoint.parent / "job_receipt.json").unlink()
    with pytest.raises(ValueError, match="receipt"):
        resolve_warm_start_parent("job_parent01", models, source, "classification", "classification:resnet18")


def test_parent_picker_rejects_corrupt_or_falsely_labeled_checkpoint(tmp_path: Path):
    models, source, checkpoint, _ = _parent(tmp_path)
    checkpoint.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError, match="checkpoint"):
        resolve_warm_start_parent("job_parent01", models, source, "classification", "classification:resnet18")
    torch.save({"task": "segmentation", "classes": ["OK", "NG"],
                "backbone": "resnet18", "model_state_dict": {}}, checkpoint)
    with pytest.raises(ValueError, match="checkpoint"):
        resolve_warm_start_parent("job_parent01", models, source, "classification", "classification:resnet18")


def test_loading_parent_rechecks_hash_classes_and_strict_weights(tmp_path: Path):
    models, source, checkpoint, old_model = _parent(tmp_path)
    parent = resolve_warm_start_parent(
        "job_parent01", models, source, task="classification", architecture="classification:resnet18",
    )
    candidate = nn.Linear(3, 2)
    load_parent_weights(candidate, parent, classes=["OK", "NG"])
    assert torch.equal(candidate.weight, old_model.weight)
    with pytest.raises(ValueError, match="classes"):
        load_parent_weights(nn.Linear(3, 2), parent, classes=["NG", "OK"])
    checkpoint.write_bytes(checkpoint.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="SHA-256"):
        load_parent_weights(nn.Linear(3, 2), parent, classes=["OK", "NG"])


def test_loading_parent_rejects_shape_mismatch_without_partial_loading(tmp_path: Path):
    models, source, _, _ = _parent(tmp_path)
    parent = resolve_warm_start_parent(
        "job_parent01", models, source, task="classification", architecture="classification:resnet18",
    )
    candidate = nn.Linear(4, 2)
    before = candidate.weight.detach().clone()
    with pytest.raises(ValueError, match="weights"):
        load_parent_weights(candidate, parent, classes=["OK", "NG"])
    assert torch.equal(candidate.weight, before)


def test_patch_retraining_loads_parent_before_optimizer_and_saves_lineage(tmp_path: Path, monkeypatch):
    from backend.engine import trainer as trainer_module

    models, source, checkpoint, _ = _parent(tmp_path, task="patch_classification")
    entries = []
    for split in ("train", "val"):
        for label, color in (("OK", 20), ("NG", 220)):
            image = source / "images" / f"{split}_{label}.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32), color=(color, 1 if split == "val" else 0, 0)).save(image)
            entries.append({
                "image": f"images/{image.name}", "box": [0, 0, 16, 16],
                "label": label, "split": split,
                "source_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            })
    (source / "patches.json").write_text(json.dumps({
        "version": 1, "classes": ["OK", "NG"], "normal_class": "OK",
        "patch_size": 16, "stride": 16, "patches": entries,
    }))
    old_model = nn.Sequential(nn.Flatten(), nn.Linear(3 * 64 * 64, 2))
    with torch.no_grad():
        old_model[1].weight.fill_(0.125)
        old_model[1].bias.fill_(0.25)
    torch.save({
        "task": "patch_classification", "classes": ["OK", "NG"],
        "backbone": "resnet18", "model_state_dict": old_model.state_dict(),
    }, checkpoint)
    parent = resolve_warm_start_parent(
        "job_parent01", models, source, "patch_classification", "patch_classification:resnet18",
    )
    monkeypatch.setattr(trainer_module, "create_classification_model", lambda **_: nn.Sequential(
        nn.Flatten(), nn.Linear(3 * 64 * 64, 2),
    ))
    optimizer_factory = trainer_module.create_optimizer_and_scheduler
    initial_weights = []

    def inspect_initial_weights(*, model, **kwargs):
        initial_weights.append(model[1].weight.detach().clone())
        return optimizer_factory(model=model, **kwargs)

    monkeypatch.setattr(trainer_module, "create_optimizer_and_scheduler", inspect_initial_weights)
    candidate_dir = models / "job_candidate01"
    trainer = trainer_module.UnifiedAutoMLTrainer(
        task="patch_classification", dataset_path=source, output_dir=candidate_dir,
        device="cpu", config_overrides={"epochs": 1, "batch_size": 2, "image_size": 64, "pretrained": False},
        warm_start=parent,
    )
    result = trainer.train(job_id="job_candidate01")
    assert result["status"] == "completed"
    assert len(initial_weights) == 1
    assert torch.equal(initial_weights[0], old_model[1].weight)
    meta = json.loads((candidate_dir / "model_meta.json").read_text())
    saved = torch.load(candidate_dir / "best_model.pt", map_location="cpu", weights_only=True)
    assert meta["warm_start"]["parent_job_id"] == "job_parent01"
    assert meta["warm_start"]["parent_checkpoint_sha256"] == parent.checkpoint_sha256
    assert saved["warm_start"] == meta["warm_start"]


def test_training_api_only_accepts_project_parent_and_records_lineage(tmp_path: Path, monkeypatch):
    import httpx
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import routes_project, routes_training

    original_init = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__", lambda self, *args, app=None, **kwargs:
                        original_init(self, *args, **kwargs))

    models, source, checkpoint, _ = _parent(tmp_path)
    for split in ("train", "val"):
        for label in ("OK", "NG"):
            folder = source / split / label
            folder.mkdir(parents=True)
            Image.new("RGB", (16, 16), color=(100 if label == "OK" else 200, 0, 0)).save(folder / f"{split}_{label}.png")
    app = FastAPI()
    app.state.project_dir = tmp_path / "projects"
    app.include_router(routes_project.router)
    app.include_router(routes_training.router)
    client = TestClient(app)
    created = client.post("/api/project/create", json={
        "name": "Retraining QA", "task": "classification", "project_dir": str(tmp_path / "project"),
    })
    assert created.status_code == 200, created.text
    updated = client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    assert updated.status_code == 200, updated.text
    captured = []

    def capture(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"], status="running", phase=None)

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", capture)
    parents = client.get("/api/training/warm-start-parents", params={
        "dataset_path": str(source), "task": "classification", "preset": "fast",
    })
    assert parents.status_code == 200, parents.text
    assert [row["job_id"] for row in parents.json()["parents"]] == ["job_parent01"]
    started = client.post("/api/training/start", json={
        "task": "classification", "dataset_path": str(source), "warm_start_job_id": "job_parent01",
    })
    assert started.status_code == 200, started.text
    assert captured[-1]["warm_start"].checkpoint_sha256 == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert started.json()["warm_start_parent_job_id"] == "job_parent01"

    other_source = tmp_path / "other"
    other_source.mkdir()
    rejected = client.post("/api/training/start", json={
        "task": "classification", "dataset_path": str(other_source), "warm_start_job_id": "job_parent01",
    })
    assert rejected.status_code in (409, 422)
    assert len(captured) == 1


def test_completed_candidate_receipt_keeps_parent_lineage(tmp_path: Path):
    from backend.api.routes_training import JobRecord, _write_job_receipt

    models, source, _, _ = _parent(tmp_path)
    parent = resolve_warm_start_parent(
        "job_parent01", models, source, "classification", "classification:resnet18",
    )
    output = models / "job_candidate01"
    record = JobRecord(
        job_id="job_candidate01", task="classification", preset="fast",
        dataset_path=str(source), output_dir=str(output), status="completed",
        source_dataset_path=str(source), dataset_fingerprint="v1:new-source",
        warm_start=parent,
    )
    _write_job_receipt(record)
    receipt = json.loads((output / "job_receipt.json").read_text())
    assert receipt["warm_start"] == parent.lineage()
    assert receipt["dataset_fingerprint"] == "v1:new-source"
