"""Evaluation should reopen a completed checkpoint after an app restart."""

import json
import time
from types import SimpleNamespace

import pytest
import torch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api import routes_evaluation
from backend.engine import exporter
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.main import create_app


def test_latest_completed_model_on_disk_resolves_with_its_prepared_dataset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})

    job_dir = tmp_path / "models" / "job_123_abc123"
    (job_dir / "dataset" / "images" / "val").mkdir(parents=True)
    (job_dir / "best_model.pt").write_bytes(b"checkpoint placeholder")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")

    output_dir, model_pt, meta, task, job_id, dataset = routes_evaluation._resolve_job_artifacts()

    assert output_dir == job_dir
    assert model_pt == job_dir / "best_model.pt"
    assert task == "segmentation"
    assert job_id == "job_123_abc123"
    assert dataset == (job_dir / "dataset").resolve()


def test_explicit_completed_model_on_disk_resolves_prepared_dataset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})

    job_dir = tmp_path / "models" / "job_456_def456"
    (job_dir / "dataset" / "images" / "test").mkdir(parents=True)
    (job_dir / "best_model.pt").write_bytes(b"checkpoint placeholder")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")

    _, _, _, _, job_id, dataset = routes_evaluation._resolve_job_artifacts("job_456_def456")

    assert job_id == "job_456_def456"
    assert dataset == (job_dir / "dataset").resolve()


def test_disk_recovery_ignores_failed_job_receipt(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})

    for job_id, status in (("job_123_good", "completed"), ("job_456_failed", "failed")):
        job_dir = tmp_path / "models" / job_id
        (job_dir / "dataset" / "images" / "val").mkdir(parents=True)
        (job_dir / "best_model.pt").write_bytes(b"checkpoint placeholder")
        (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")
        (job_dir / "job_receipt.json").write_text(json.dumps({"status": status}), encoding="utf-8")

    *_, resolved_id, _ = routes_evaluation._resolve_job_artifacts()
    assert resolved_id == "job_123_good"


def test_stopping_training_job_cannot_be_evaluated_even_with_checkpoint(monkeypatch, tmp_path):
    job_dir = tmp_path / "job_stopping"
    (job_dir / "dataset").mkdir(parents=True)
    (job_dir / "best_model.pt").write_bytes(b"partial checkpoint")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")
    record = SimpleNamespace(
        status="stopping", output_dir=str(job_dir), dataset_path=str(job_dir / "dataset"),
        task="segmentation", job_id="job_stopping",
    )
    monkeypatch.setattr(routes_evaluation.training_job_manager, "get_job", lambda job_id: record)

    with pytest.raises(HTTPException) as error:
        routes_evaluation._resolve_job_artifacts("job_stopping")

    assert error.value.status_code == 400


@pytest.mark.parametrize("job_id", ["../outside", "job_123_safe/../../outside", "/tmp/outside/best_model.pt"])
def test_checkpoint_job_id_cannot_be_a_path(monkeypatch, tmp_path, job_id):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})
    (tmp_path / "models" / "job_123_safe").mkdir(parents=True)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "best_model.pt").write_bytes(b"untrusted checkpoint")

    assert routes_evaluation._find_model_file(job_id) is None
    assert exporter.locate_checkpoint(job_id) is None
    with pytest.raises(HTTPException) as error:
        routes_evaluation._resolve_job_artifacts(job_id)
    assert error.value.status_code in (400, 404)


@pytest.mark.parametrize("symlink_part", ["models_root", "job_dir", "checkpoint"])
def test_checkpoint_lookup_rejects_symlinks_outside_models(monkeypatch, tmp_path, symlink_part):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})
    job_id = "job_123_safe"
    models = tmp_path / "models"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "best_model.pt").write_bytes(b"untrusted checkpoint")
    if symlink_part == "models_root":
        (outside / job_id).mkdir()
        (outside / job_id / "best_model.pt").write_bytes(b"untrusted checkpoint")
        models.symlink_to(outside, target_is_directory=True)
    elif symlink_part == "job_dir":
        models.mkdir()
        (models / job_id).symlink_to(outside, target_is_directory=True)
    else:
        models.mkdir()
        (models / job_id).mkdir()
        (models / job_id / "best_model.pt").symlink_to(outside / "best_model.pt")

    assert routes_evaluation._find_model_file(job_id) is None
    assert exporter.locate_checkpoint(job_id) is None
    with pytest.raises(HTTPException) as error:
        routes_evaluation._resolve_job_artifacts(job_id)
    assert error.value.status_code in (400, 404)


def test_api_rejects_external_checkpoint_before_loading(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})
    external = tmp_path / "outside"
    external.mkdir()
    (external / "best_model.pt").write_bytes(b"untrusted checkpoint")
    monkeypatch.setattr(exporter, "load_checkpoint_and_reconstruct_model", lambda path: pytest.fail("untrusted load"))
    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})

    benchmark = client.post("/api/evaluation/benchmark", json={"job_id": str(external), "resolution": 32})
    runtime = client.post("/api/export/runtime", json={"job_id": str(external), "resolution": 32})

    assert benchmark.status_code == 404
    assert runtime.status_code == 404


def test_completed_record_cannot_override_checkpoint_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    external = tmp_path / "outside" / "job_123_safe"
    external.mkdir(parents=True)
    (external / "best_model.pt").write_bytes(b"untrusted checkpoint")
    record = SimpleNamespace(
        job_id="job_123_safe", status="completed", output_dir=str(external),
        dataset_path=str(tmp_path), task="segmentation",
    )
    monkeypatch.setattr(routes_evaluation.training_job_manager, "get_job", lambda job_id: record)

    assert routes_evaluation._find_model_file(record.job_id) is None
    assert exporter.locate_checkpoint(record.job_id) is None
    with pytest.raises(HTTPException) as error:
        routes_evaluation._resolve_job_artifacts(record.job_id)
    assert error.value.status_code == 404


def test_project_job_checkpoint_is_allowed_inside_fixed_layout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})
    job_id = "job_123_safe"
    job_dir = tmp_path / "projects" / job_id / "models"
    (job_dir / "dataset").mkdir(parents=True)
    (job_dir / "best_model.pt").write_bytes(b"checkpoint placeholder")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")

    assert routes_evaluation._find_model_file(job_id) == job_dir / "best_model.pt"
    assert exporter.locate_checkpoint(job_id) == job_dir / "best_model.pt"
    *_, resolved_id, _ = routes_evaluation._resolve_job_artifacts(job_id)
    assert resolved_id == job_id
    *_, latest_id, _ = routes_evaluation._resolve_job_artifacts()
    assert latest_id == job_id


def test_disk_recovery_selects_only_matching_source_folder_and_task(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, "_jobs", {})
    for name in ("A", "B"):
        source = tmp_path / "source" / name
        source.mkdir(parents=True)
        (source / "image.jpg").write_bytes(f"image-{name}".encode())
        job_id = f"job_123_{name}"
        job_dir = tmp_path / "models" / job_id
        (job_dir / "dataset" / "images" / "val").mkdir(parents=True)
        (job_dir / "best_model.pt").write_bytes(b"checkpoint placeholder")
        (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")
        (job_dir / "job_receipt.json").write_text(json.dumps({
            "job_id": job_id, "status": "completed", "task": "segmentation",
            "dataset_path": str(job_dir / "dataset"), "output_dir": str(job_dir),
            "source_dataset_path": str(source),
            "dataset_fingerprint": fingerprint_dataset(source),
        }), encoding="utf-8")
        (job_dir / "dataset" / "source_manifest.json").write_text(json.dumps([
            {"source_image": str(source / "image.jpg"), "image": "sample.png"},
        ]), encoding="utf-8")

    *_, b_id, _ = routes_evaluation._resolve_job_artifacts(
        source_dataset_path=str(tmp_path / "source" / "B"), source_task="segmentation",
    )
    *_, a_id, _ = routes_evaluation._resolve_job_artifacts(
        source_dataset_path=str(tmp_path / "source" / "A"), source_task="segmentation",
    )
    assert b_id == "job_123_B"
    assert a_id == "job_123_A"

    with pytest.raises(HTTPException) as wrong_folder:
        routes_evaluation._resolve_job_artifacts(
            "job_123_A", source_dataset_path=str(tmp_path / "source" / "B"),
            source_task="segmentation",
        )
    assert wrong_folder.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_task:
        routes_evaluation._resolve_job_artifacts(
            source_dataset_path=str(tmp_path / "source" / "A"), source_task="classification",
        )
    assert wrong_task.value.status_code == 404

    # A source edit at the same path must invalidate automatic and explicit recovery.
    (tmp_path / "source" / "B" / "image.jpg").write_bytes(b"changed source image")
    with pytest.raises(HTTPException) as changed_source:
        routes_evaluation._resolve_job_artifacts(
            source_dataset_path=str(tmp_path / "source" / "B"), source_task="segmentation",
        )
    assert changed_source.value.status_code == 404
    with pytest.raises(HTTPException) as changed_explicit:
        routes_evaluation._resolve_job_artifacts(
            "job_123_B", source_dataset_path=str(tmp_path / "source" / "B"), source_task="segmentation",
        )
    assert changed_explicit.value.status_code == 404

    # A pre-versioning receipt cannot prove that source data stayed unchanged.
    legacy_receipt = tmp_path / "models" / "job_123_A" / "job_receipt.json"
    legacy = json.loads(legacy_receipt.read_text(encoding="utf-8"))
    legacy.pop("dataset_fingerprint")
    legacy_receipt.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(HTTPException) as unversioned:
        routes_evaluation._resolve_job_artifacts(
            source_dataset_path=str(tmp_path / "source" / "A"), source_task="segmentation",
        )
    assert unversioned.value.status_code == 404


def test_results_api_passes_source_filter_separately_from_evaluation_dataset(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(routes_evaluation, "run_or_load_evaluation", lambda **kwargs: calls.append(kwargs) or {"job_id": "job_A"})
    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})

    response = client.get("/api/evaluation/results", params={
        "source_dataset_path": "/source/A", "source_task": "segmentation",
    })

    assert response.status_code == 200
    assert calls[0]["source_dataset_path"] == "/source/A"
    assert calls[0]["source_task"] == "segmentation"
    assert calls[0]["dataset_path"] is None


def test_benchmark_times_a_preloaded_model_instead_of_reloading_checkpoint(monkeypatch, tmp_path):
    from backend.engine import exporter

    checkpoint = tmp_path / "best_model.pt"
    torch.save({"task": "segmentation"}, checkpoint)
    monkeypatch.setattr(routes_evaluation, "_find_model_file", lambda job_id: checkpoint)
    monkeypatch.setattr(routes_evaluation, "get_device", lambda: torch.device("cpu"))

    calls = {"load": 0, "forward": 0}

    class TinyModel(torch.nn.Module):
        def forward(self, batch):
            calls["forward"] += 1
            time.sleep(0.001)
            return batch.mean(dim=1)

    def load_model(path):
        calls["load"] += 1
        assert path == checkpoint
        return TinyModel(), {"task": "segmentation"}, None

    monkeypatch.setattr(exporter, "load_checkpoint_and_reconstruct_model", load_model)
    monkeypatch.setattr(routes_evaluation, "infer", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("infer reloads the model")))

    result = routes_evaluation.run_inference_benchmark(
        routes_evaluation.BenchmarkRequest(job_id="completed", iterations=5, resolution=32)
    )

    assert calls == {"load": 1, "forward": 8}
    assert result["measurement_scope"] == "model_forward_only"
    assert result["iterations"] == 5
    assert result["mean_latency_ms"] > 0


def test_runtime_export_rejects_package_path_traversal_before_writing(monkeypatch, tmp_path):
    from backend.engine import exporter

    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"placeholder")
    monkeypatch.setattr(exporter, "locate_checkpoint", lambda job_id: checkpoint)

    with pytest.raises(ValueError, match="package name"):
        exporter.export_runtime_package(
            job_id="completed", package_name="../outside", output_base_dir=tmp_path / "exports"
        )

    assert not (tmp_path / "outside").exists()


def test_repeated_runtime_export_keeps_each_format_in_a_separate_package(monkeypatch, tmp_path):
    from backend.engine import exporter

    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"placeholder")
    monkeypatch.setattr(exporter, "locate_checkpoint", lambda job_id: checkpoint)
    model = torch.nn.Conv2d(3, 2, kernel_size=1).eval()
    monkeypatch.setattr(
        exporter,
        "load_checkpoint_and_reconstruct_model",
        lambda path: (model, {"task": "segmentation", "classes": ["background", "defect"]}, None),
    )

    first = exporter.export_runtime_package(
        job_id="completed", package_name="same_name", export_format="onnx",
        resolution=32, output_base_dir=tmp_path / "exports",
    )
    second = exporter.export_runtime_package(
        job_id="completed", package_name="same_name", export_format="torchscript",
        resolution=32, output_base_dir=tmp_path / "exports",
    )

    assert first["package_path"] != second["package_path"]
    assert (tmp_path / "exports" / first["package_name"] / "model.onnx").is_file()
    assert [item["name"] for item in second["manifest"]] == [
        "README_DEPLOY.md", "config.json", "infer.py", "model.pt", "requirements.txt"
    ]
