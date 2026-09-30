"""Relocation must retain valid models for every registered label set."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_training
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.project_labelsets import labelset_root
from backend.main import create_app


def _save_label(client, image, label):
    response = client.post("/api/annotations/save", json={
        "image_id": image.stem, "image_path": str(image), "image_width": 24, "image_height": 24,
        "annotations": [{"type": "bbox", "label": label, "bbox": [1, 1, 12, 12]}],
    })
    assert response.status_code == 200, response.text


def _completed_job(project, source, labelset_id, job_id):
    project_dir = Path(project["project_dir"])
    directory = project_dir / "models" / job_id
    (directory / "dataset").mkdir(parents=True)
    (directory / "dataset" / "prepared.txt").write_text("prepared labels")
    (directory / "best_model.pt").write_bytes(job_id.encode())
    (directory / "model_meta.json").write_text(json.dumps({"task": "segmentation"}))
    fingerprint = fingerprint_dataset(source, studio_root=labelset_root(project_dir, labelset_id), use_scope=False)
    (directory / "job_receipt.json").write_text(json.dumps({
        "job_id": job_id, "task": "segmentation", "status": "completed",
        "source_dataset_path": str(source), "dataset_fingerprint": fingerprint,
        "dataset_path": str(directory / "dataset"), "output_dir": str(directory),
    }))
    return fingerprint


def _backup_with_two_labelsets(tmp_path, monkeypatch, *, stale_default=False):
    monkeypatch.setattr(routes_training.training_job_manager, "_jobs", {})
    source = (tmp_path / "images").resolve()
    source.mkdir()
    image = source / "part.png"
    Image.new("RGB", (24, 24), "white").save(image)
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.post("/api/project/create", json={"name": "Label set backup", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    _save_label(client, image, "Default defect")
    default_fingerprint = _completed_job(project, source, "default", "job_default_set")
    if stale_default:
        _save_label(client, image, "Changed after default training")
    second = client.post("/api/project/labelsets", json={"name": "Review B"}).json()["id"]
    assert client.put(f"/api/project/labelsets/{second}/activate").status_code == 200
    _save_label(client, image, "B defect")
    second_fingerprint = _completed_job(project, source, second, "job_second_set")
    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backups")})
    assert backed.status_code == 200, backed.text
    return client, Path(backed.json()["archive_path"]), second, default_fingerprint, second_fingerprint


def _verify(client, source, job_id):
    return client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": source, "models": [{"job_id": job_id, "task": "segmentation"}],
    })


@pytest.mark.parametrize("stale_default", [False, True])
def test_restore_relocates_each_current_labelset_fingerprint_without_reviving_stale_models(tmp_path, monkeypatch, stale_default):
    client, archive, second, old_default, old_second = _backup_with_two_labelsets(
        tmp_path, monkeypatch, stale_default=stale_default,
    )
    restored = client.post("/api/project/restore", json={"archive_path": str(archive), "target_dir": str(tmp_path / "restored")})
    assert restored.status_code == 200, restored.text
    source = restored.json()["source_dataset_dir"]
    second_result = _verify(client, source, "job_second_set")
    assert second_result.status_code == 200, second_result.text
    assert client.put("/api/project/labelsets/default/activate").status_code == 200
    default_result = _verify(client, source, "job_default_set")
    assert default_result.status_code == (409 if stale_default else 200), default_result.text
    assert _verify(client, source, "job_second_set").status_code == 409
    assert client.put(f"/api/project/labelsets/{second}/activate").status_code == 200
    assert _verify(client, source, "job_second_set").status_code == 200
    default_receipt = json.loads((tmp_path / "restored" / "models" / "job_default_set" / "job_receipt.json").read_text())
    assert (default_receipt["dataset_fingerprint"] == old_default) is stale_default
    second_receipt = json.loads((tmp_path / "restored" / "models" / "job_second_set" / "job_receipt.json").read_text())
    assert second_receipt["dataset_fingerprint"] != old_second


def test_legacy_backup_without_labelset_fingerprint_map_still_restores_active_model(tmp_path, monkeypatch):
    client, archive, second, _old_default, _old_second = _backup_with_two_labelsets(tmp_path, monkeypatch)
    legacy = tmp_path / "legacy.mvision.zip"
    with ZipFile(archive) as original, ZipFile(legacy, "w") as output:
        for member in original.infolist():
            raw = original.read(member.filename)
            if member.filename == "backup-manifest.json":
                manifest = json.loads(raw)
                manifest.pop("source_dataset_fingerprints_by_labelset", None)
                manifest.pop("source_active_labelset_id", None)
                raw = json.dumps(manifest).encode()
            output.writestr(member, raw)
    restored = client.post("/api/project/restore", json={"archive_path": str(legacy), "target_dir": str(tmp_path / "legacy_restored")})
    assert restored.status_code == 200, restored.text
    assert restored.json()["active_labelset_id"] == second
    result = _verify(client, restored.json()["source_dataset_dir"], "job_second_set")
    assert result.status_code == 200, result.text
