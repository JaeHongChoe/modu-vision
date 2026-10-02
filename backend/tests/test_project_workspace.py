"""Project switching must survive daemon restarts without overwriting workspaces."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api import routes_project
from backend.api import routes_training
from backend.api.routes_evaluation import _resolve_job_artifacts
from backend.engine.checkpoint_paths import trusted_checkpoint


_original_httpx_init = httpx.Client.__init__


def _compatible_httpx_init(self, *args, app=None, **kwargs):
    return _original_httpx_init(self, *args, **kwargs)


httpx.Client.__init__ = _compatible_httpx_init


@pytest.fixture
def workspace(tmp_path: Path):
    root = tmp_path / "projects"
    root.mkdir()

    def client() -> TestClient:
        from backend.contracts.context import ContextRegistry
        from backend.main import ProjectContextMiddleware
        app = FastAPI()
        app.state.project_dir = root
        app.state.context_registry = ContextRegistry(root)
        app.include_router(routes_project.router)
        app.include_router(routes_training.router)
        app.add_middleware(ProjectContextMiddleware, project_app=app)  # a start is recorded in its project's job ledger
        return TestClient(app)

    return client, root


def test_project_create_switch_and_restart_restores_last_opened(workspace):
    client_factory, root = workspace
    client = client_factory()
    first = client.post("/api/project/create", json={"name": "Ceramic inspection", "task": "segmentation"}).json()
    second = client.post("/api/project/create", json={"name": "Wafer inspection", "task": "detection"}).json()
    assert first["id"] != second["id"]
    assert client.get("/api/project/current").json()["id"] == second["id"]

    reopened = client.post("/api/project/open", json={"project_dir": first["project_dir"]})
    assert reopened.status_code == 200
    assert reopened.json()["id"] == first["id"]

    # A fresh FastAPI app represents a backend restart, including its in-memory state.
    restarted = client_factory()
    assert restarted.get("/api/project/current").json()["id"] == first["id"]
    assert restarted.get("/api/project/list").json()["projects"][0]["id"] == first["id"]
    assert (root / "ceramic_inspection" / "project.json").exists()


def test_active_project_state_is_scoped_to_each_backend_app(workspace, tmp_path):
    client_factory, _ = workspace
    first_client = client_factory()
    first = first_client.post("/api/project/create", json={"name": "Factory A"}).json()

    other_root = tmp_path / "other_instance"
    other_root.mkdir()
    other_app = FastAPI()
    other_app.state.project_dir = other_root
    other_app.include_router(routes_project.router)
    other_client = TestClient(other_app)

    assert other_client.get("/api/project/current").json()["id"] != first["id"]
    assert first_client.get("/api/project/current").json()["id"] == first["id"]
    assert all(item["id"] != first["id"] for item in other_client.get("/api/project/list").json()["projects"])
    assert first_client.get("/api/project/list").json()["projects"][0]["id"] == first["id"]


def test_existing_project_cannot_be_overwritten_by_create(workspace):
    client_factory, _ = workspace
    client = client_factory()
    first = client.post("/api/project/create", json={"name": "Persistent work", "task": "classification"}).json()
    config_file = Path(first["project_dir"]) / "project.json"
    before = config_file.read_bytes()

    retry = client.post("/api/project/create", json={"name": "Persistent work", "task": "anomaly"})
    assert retry.status_code == 409
    assert config_file.read_bytes() == before
    assert client.get("/api/project/current").json()["id"] == first["id"]


def test_open_rejects_dataset_folder_without_project_manifest(workspace, tmp_path):
    client_factory, _ = workspace
    folder = tmp_path / "image_dataset"
    folder.mkdir()
    (folder / "sample.jpg").write_bytes(b"image")
    client = client_factory()

    response = client.post("/api/project/open", json={"project_dir": str(folder)})
    assert response.status_code == 422
    assert not (folder / "project.json").exists()


def test_dataset_source_and_task_are_saved_and_reopened(workspace, tmp_path):
    client_factory, _ = workspace
    client = client_factory()
    created = client.post("/api/project/create", json={"name": "QC data", "task": "classification"}).json()
    source_dir = tmp_path / "raw_images"
    source_dir.mkdir()

    update = client.put("/api/project/update", json={"task": "segmentation", "source_dataset_dir": str(source_dir)})
    assert update.status_code == 200
    assert update.json()["source_dataset_dir"] == str(source_dir)
    assert update.json()["task"] == "segmentation"
    saved = json.loads((Path(created["project_dir"]) / "project.json").read_text())
    assert saved["source_dataset_dir"] == str(source_dir)

    restarted = client_factory()
    restored = restarted.get("/api/project/current").json()
    assert restored["source_dataset_dir"] == str(source_dir)
    assert restored["task"] == "segmentation"


def test_project_inside_source_is_rejected_before_source_link_is_saved(workspace, tmp_path):
    client_factory, _ = workspace
    source = tmp_path / "source"
    source.mkdir()
    nested_project = source / "workspace"
    client = client_factory()
    created = client.post("/api/project/create", json={"name": "Nested", "project_dir": str(nested_project)}).json()
    update = client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    assert update.status_code == 422
    assert client.get("/api/project/current").json()["source_dataset_dir"] is None
    assert Path(created["project_dir"]).is_dir()


def test_training_uses_active_project_models_by_default_but_honors_explicit_path(workspace, tmp_path, monkeypatch):
    from PIL import Image

    client_factory, _ = workspace
    client = client_factory()
    project = client.post("/api/project/create", json={"name": "Model workspace", "task": "classification"}).json()
    source = tmp_path / "classification"
    for split in ("train", "val"):
        for label in ("OK", "NG"):
            folder = source / split / label
            folder.mkdir(parents=True)
            Image.new("RGB", (16, 16), "white").save(folder / f"{split}_{label}.png")

    captured = []

    def record_start(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", record_start)
    request = {"task": "classification", "dataset_path": str(source)}
    default = client.post("/api/training/start", json=request)
    assert default.status_code == 200, default.text
    assert Path(default.json()["output_dir"]).parent == Path(project["models_dir"])

    explicit_dir = tmp_path / "external_models"
    explicit = client.post("/api/training/start", json={**request, "output_dir": str(explicit_dir)})
    assert explicit.status_code == 200, explicit.text
    assert Path(explicit.json()["output_dir"]).parent == explicit_dir
    assert len(captured) == 2
    assert client.get("/api/project/current").json()["source_dataset_dir"] == str(source)
    assert all(row["dataset_binding"]["dataset_version_id"].startswith("v_") for row in captured)

    other = tmp_path / "other_classification"
    import shutil
    shutil.copytree(source, other)
    rejected = client.post("/api/training/start", json={**request, "dataset_path": str(other)})
    assert rejected.status_code == 409, rejected.text
    assert len(captured) == 2
    assert client.get("/api/project/current").json()["source_dataset_dir"] == str(source)


def test_completed_checkpoint_in_active_project_is_trusted(workspace):
    client_factory, _ = workspace
    client = client_factory()
    project = client.post("/api/project/create", json={"name": "Checkpoint project"}).json()
    job_id = "job_123456_abcd12"
    job_dir = Path(project["models_dir"]) / job_id
    job_dir.mkdir()
    checkpoint = job_dir / "best_model.pt"
    checkpoint.write_bytes(b"completed checkpoint")
    (job_dir / "job_receipt.json").write_text(json.dumps({"status": "completed", "job_id": job_id}))

    assert trusted_checkpoint(job_id, str(job_dir)) == checkpoint

    client.post("/api/project/create", json={"name": "Another project"})
    assert trusted_checkpoint(job_id, str(job_dir)) is None
    assert trusted_checkpoint(job_id, project_models_dir=project["models_dir"]) == checkpoint


def test_completed_project_model_is_reopened_after_job_manager_restart(workspace, tmp_path):
    client_factory, _ = workspace
    client = client_factory()
    project = client.post("/api/project/create", json={"name": "Recovered model"}).json()
    source = tmp_path / "source"
    source.mkdir()
    job_id = "job_123457_cdef34"
    job_dir = Path(project["models_dir"]) / job_id
    job_dir.mkdir()
    (job_dir / "best_model.pt").write_bytes(b"checkpoint marker")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}))
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "job_id": job_id, "task": "classification",
        "dataset_path": str(source), "source_dataset_path": str(source),
    }))

    assert _resolve_job_artifacts(job_id=job_id)[4] == job_id
    assert _resolve_job_artifacts()[4] == job_id
