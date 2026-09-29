"""Immutable, source-mapped dataset and label versions without copying images."""

import json
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api import routes_dataset, routes_dataset_versions, routes_project
from backend.engine.annotation_storage import dataset_annotation_dir


_original_httpx_init = httpx.Client.__init__


def _compatible_httpx_init(self, *args, app=None, **kwargs):
    return _original_httpx_init(self, *args, **kwargs)


httpx.Client.__init__ = _compatible_httpx_init


@pytest.fixture
def version_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", tmp_path / "annotations")
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "splits")
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.jpg").write_bytes(b"first image bytes" * 100)
    (source / "second.jpg").write_bytes(b"second image bytes" * 100)
    labelme = source / "first.json"
    labelme.write_text(json.dumps({"shapes": [{"label": "NG"}]}))
    studio = dataset_annotation_dir(source, routes_dataset.STUDIO_ANNOTATIONS_DIR)
    studio.mkdir(parents=True)
    (studio / "first.json").write_text(json.dumps({"annotations": [{"label": "Scratch"}]}))
    split = routes_dataset._split_manifest_file(source)
    split.parent.mkdir(parents=True)
    split.write_text(json.dumps({"assignments": {"first.jpg": "train", "second.jpg": "val"}}))

    app = FastAPI()
    app.state.project_dir = tmp_path / "projects"
    app.include_router(routes_project.router)
    app.include_router(routes_dataset_versions.router)
    client = TestClient(app)
    project = client.post("/api/project/create", json={"name": "Ceramic QC", "task": "segmentation"}).json()
    updated = client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    assert updated.status_code == 200
    return client, project, source, studio, split, labelme


def test_version_manifest_maps_and_hashes_source_without_copying_images(version_workspace):
    client, project, source, studio, split, _ = version_workspace
    created = client.post("/api/dataset/versions", json={"name": "Before relabeling"})
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["name"] == "Before relabeling"
    assert item["image_count"] == 2
    assert item["label_file_count"] >= 2
    assert item["source_dataset_dir"] == str(source)
    assert item["status"] == "verified"

    version_dir = Path(project["project_dir"]) / "versions" / item["id"]
    manifest = json.loads((version_dir / "manifest.json").read_text())
    image_rows = [row for row in manifest["files"] if row["kind"] == "image"]
    assert {row["relative_path"] for row in image_rows} == {"first.jpg", "second.jpg"}
    assert all(row["source_path"] == str((source / row["relative_path"]).resolve()) for row in image_rows)
    assert all(len(row["sha256"]) == 64 for row in image_rows)
    assert not list(version_dir.rglob("*.jpg"))
    assert (version_dir / "labels" / "source" / "first.json").read_bytes() == (source / "first.json").read_bytes()
    assert (version_dir / "labels" / "studio" / "first.json").read_bytes() == (studio / "first.json").read_bytes()
    assert (version_dir / "labels" / "split" / "manifest.json").read_bytes() == split.read_bytes()

    listed = client.get("/api/dataset/versions")
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()["versions"]] == [item["id"]]
    verified = client.get(f"/api/dataset/versions/{item['id']}/verify")
    assert verified.json()["status"] == "verified"
    assert verified.json()["changed_files"] == []


def test_restore_creates_backup_and_replaces_editable_labels_and_split(version_workspace):
    client, _, source, studio, split, labelme = version_workspace
    source_label_bytes = labelme.read_bytes()
    baseline_overlay = (studio / "first.json").read_bytes()
    baseline_split = split.read_bytes()
    version = client.post("/api/dataset/versions", json={"name": "Baseline"}).json()

    (studio / "first.json").write_text(json.dumps({"annotations": [{"label": "Changed"}]}))
    (studio / "second.json").write_text(json.dumps({"annotations": [{"label": "New"}]}))
    split.write_text(json.dumps({"assignments": {"first.jpg": "test"}}))
    restored = client.post(f"/api/dataset/versions/{version['id']}/restore")
    assert restored.status_code == 200, restored.text
    result = restored.json()
    assert result["restored_version_id"] == version["id"]
    assert result["backup_version_id"] != version["id"]
    assert (studio / "first.json").read_bytes() == baseline_overlay
    assert not (studio / "second.json").exists()
    assert split.read_bytes() == baseline_split
    assert labelme.read_bytes() == source_label_bytes
    assert len(client.get("/api/dataset/versions").json()["versions"]) == 2


def test_source_change_blocks_restore_without_touching_editable_labels(version_workspace):
    client, _, source, studio, split, _ = version_workspace
    version = client.post("/api/dataset/versions", json={"name": "Baseline"}).json()
    (source / "first.jpg").write_bytes(b"changed image")
    (studio / "first.json").write_text("working edit")
    split.write_text("working split")

    rejected = client.post(f"/api/dataset/versions/{version['id']}/restore")
    assert rejected.status_code == 409
    assert (studio / "first.json").read_text() == "working edit"
    assert split.read_text() == "working split"


def test_new_source_image_or_label_invalidates_version_and_blocks_restore(version_workspace):
    client, _, source, studio, _, _ = version_workspace
    version = client.post("/api/dataset/versions", json={"name": "Before additions"}).json()
    (studio / "first.json").write_text("working edit")
    (source / "third.jpg").write_bytes(b"new image")
    (source / "second.json").write_text(json.dumps({"shapes": []}))

    verification = client.get(f"/api/dataset/versions/{version['id']}/verify")
    assert verification.status_code == 200
    assert verification.json()["status"] == "changed"
    assert "source/third.jpg" in verification.json()["changed_files"]
    assert "source/second.json" in verification.json()["changed_files"]
    assert client.post(f"/api/dataset/versions/{version['id']}/restore").status_code == 409
    assert (studio / "first.json").read_text() == "working edit"
    assert (source / "third.jpg").read_bytes() == b"new image"


def test_source_symlink_mapping_is_preserved_and_verified(version_workspace, tmp_path):
    client, project, source, _, _, _ = version_workspace
    external = tmp_path / "external.jpg"
    external.write_bytes(b"linked inspection image")
    (source / "linked.jpg").symlink_to(external)

    created = client.post("/api/dataset/versions", json={"name": "Linked source"})
    assert created.status_code == 200, created.text
    version_dir = Path(project["project_dir"]) / "versions" / created.json()["id"]
    manifest = json.loads((version_dir / "manifest.json").read_text())
    linked = next(row for row in manifest["files"] if row["relative_path"] == "linked.jpg")
    assert linked["source_path"] == str(external.resolve())
    assert client.get(f"/api/dataset/versions/{created.json()['id']}/verify").json()["status"] == "verified"


def test_corrupted_snapshot_label_blocks_restore(version_workspace):
    client, project, _, studio, _, _ = version_workspace
    created = client.post("/api/dataset/versions", json={"name": "Baseline"}).json()
    backup = Path(project["project_dir"]) / "versions" / created["id"] / "labels" / "studio" / "first.json"
    backup.write_text("corrupted")
    (studio / "first.json").write_text("working edit")

    check = client.get(f"/api/dataset/versions/{created['id']}/verify")
    assert check.status_code == 200
    assert check.json()["status"] == "changed"
    assert any("backup/studio/first.json" in entry for entry in check.json()["changed_files"])
    assert client.post(f"/api/dataset/versions/{created['id']}/restore").status_code == 409
    assert (studio / "first.json").read_text() == "working edit"


def test_failed_restore_rolls_back_overlay_and_split(version_workspace, monkeypatch):
    client, _, _, studio, split, _ = version_workspace
    version = client.post("/api/dataset/versions", json={"name": "Baseline"}).json()
    (studio / "first.json").write_text("working edit")
    split.write_text("working split")

    original_replace = routes_dataset_versions.os.replace

    def fail_install_split(source, target):
        if str(target) == str(split) and ".restore-" in str(source):
            raise OSError("injected split installation failure")
        return original_replace(source, target)

    monkeypatch.setattr(routes_dataset_versions.os, "replace", fail_install_split)
    failed = client.post(f"/api/dataset/versions/{version['id']}/restore")
    assert failed.status_code == 500
    assert (studio / "first.json").read_text() == "working edit"
    assert split.read_text() == "working split"
