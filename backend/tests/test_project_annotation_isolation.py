"""Projects sharing images keep independent Studio labels and training inputs."""

import json
import hashlib
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from backend.engine import annotation_storage
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.api import routes_dataset, routes_training
from backend.api.routes_evaluation import _matches_source_dataset
from backend.engine.annotation_storage import reset_request_annotation_root, set_request_annotation_root
from backend.engine.dataset_loaders import reset_request_split_root, set_request_split_root
from backend.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(project_dir=str(tmp_path / "projects"))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (32, 24), "white").save(image)
    (source / "sample.json").write_text(json.dumps({
        "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "Source", "shape_type": "rectangle", "points": [[1, 1], [5, 5]]}],
    }))
    return source, image


def _save(client: TestClient, image: Path, label: str):
    return client.post("/api/annotations/save", json={
        "image_id": "sample", "image_path": str(image), "image_width": 32, "image_height": 24,
        "annotations": [{"type": "bbox", "label": label, "bbox": [2, 2, 9, 10]}],
    })


def test_two_projects_same_source_keep_separate_annotations_versions_and_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", tmp_path / "empty_legacy")
    source, image = _source(tmp_path)
    client = _client(tmp_path)

    first = client.post("/api/project/create", json={"name": "First", "task": "detection"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert _save(client, image, "First label").status_code == 200
    first_overlay = annotation_storage.dataset_annotation_dir(source, Path(first["annotations_dir"]), use_scope=False)
    assert json.loads((first_overlay / "sample.json").read_text())["annotations"][0]["label"] == "First label"
    first_version = client.post("/api/dataset/versions", json={"name": "First version"})
    assert first_version.status_code == 200, first_version.text

    second = client.post("/api/project/create", json={"name": "Second", "task": "detection"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    initial_second = client.get("/api/annotations/sample", params={"file_path": str(image)})
    assert initial_second.status_code == 200
    assert [item["label"] for item in initial_second.json()["annotations"]] == ["Source"]
    assert _save(client, image, "Second label").status_code == 200
    second_overlay = annotation_storage.dataset_annotation_dir(source, Path(second["annotations_dir"]), use_scope=False)
    assert first_overlay != second_overlay
    assert json.loads((second_overlay / "sample.json").read_text())["annotations"][0]["label"] == "Second label"

    second_import = client.post("/api/dataset/import", json={"folder_path": str(source), "task": "detection"})
    assert second_import.status_code == 200, second_import.text
    assert "Second label" in second_import.json()["classes"]
    second_version = client.post("/api/dataset/versions", json={"name": "Second version"})
    assert second_version.status_code == 200
    assert [entry["id"] for entry in client.get("/api/dataset/versions").json()["versions"]] == [second_version.json()["id"]]

    assert client.post("/api/project/open", json={"project_dir": first["project_dir"]}).status_code == 200
    reopened = client.get("/api/annotations/sample", params={"file_path": str(image)})
    assert [item["label"] for item in reopened.json()["annotations"]] == ["First label"]
    first_import = client.post("/api/dataset/import", json={"folder_path": str(source), "task": "detection"})
    assert first_import.status_code == 200
    assert "First label" in first_import.json()["classes"]
    assert [entry["id"] for entry in client.get("/api/dataset/versions").json()["versions"]] == [first_version.json()["id"]]
    assert fingerprint_dataset(source, studio_root=Path(first["annotations_dir"])) != fingerprint_dataset(source, studio_root=Path(second["annotations_dir"]))


def test_legacy_overlay_is_copied_once_without_removing_original(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy_annotations"
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", legacy_root)
    source, image = _source(tmp_path)
    legacy = annotation_storage.dataset_annotation_dir(source, legacy_root, use_scope=False)
    legacy.mkdir(parents=True)
    original = json.dumps({
        "image_id": "sample", "image_width": 32, "image_height": 24,
        "annotations": [{"type": "bbox", "label": "Legacy edit", "bbox": [2, 2, 9, 10]}],
    }).encode()
    (legacy / "sample.json").write_bytes(original)

    client = _client(tmp_path)
    project = client.post("/api/project/create", json={"name": "Migrated", "task": "detection"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    scoped = annotation_storage.dataset_annotation_dir(source, Path(project["annotations_dir"]), use_scope=False)
    assert (scoped / "sample.json").read_bytes() == original
    assert (legacy / "sample.json").read_bytes() == original
    assert [item["label"] for item in client.get("/api/annotations/sample", params={"file_path": str(image)}).json()["annotations"]] == ["Legacy edit"]

    # Reopening does not overwrite a project edit with the old global file.
    assert _save(client, image, "Project edit").status_code == 200
    assert client.post("/api/project/open", json={"project_dir": project["project_dir"]}).status_code == 200
    assert json.loads((scoped / "sample.json").read_text())["annotations"][0]["label"] == "Project edit"
    assert (legacy / "sample.json").read_bytes() == original


def test_gallery_label_status_tracks_project_annotation_overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", tmp_path / "empty_legacy")
    source, original = _source(tmp_path)
    added = source / "unlabeled.png"
    Image.new("RGB", (32, 24), "white").save(added)
    client = _client(tmp_path)
    client.post("/api/project/create", json={"name": "Gallery", "task": "segmentation"})
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200

    def names(status):
        response = client.get("/api/dataset/images", params={
            "folder_path": str(source), "task": "segmentation", "label_status": status,
        })
        assert response.status_code == 200, response.text
        return response.json()["total"], {row["file_name"] for row in response.json()["items"]}

    assert names("labeled") == (1, {original.name})
    assert names("unlabeled") == (1, {added.name})
    assert client.post("/api/annotations/save", json={
        "image_id": original.stem, "image_path": str(original), "image_width": 32, "image_height": 24,
        "annotations": [],
    }).status_code == 200
    assert client.post("/api/annotations/save", json={
        "image_id": added.stem, "image_path": str(added), "image_width": 32, "image_height": 24,
        "annotations": [{"type": "bbox", "label": "New label", "bbox": [2, 2, 9, 10]}],
    }).status_code == 200
    assert names("labeled") == (1, {added.name})
    assert names("unlabeled") == (1, {original.name})
    current = client.get("/api/dataset/images", params={
        "folder_path": str(source), "task": "segmentation", "label_status": "labeled",
    }).json()["items"][0]
    assert current["label"] == "New label"


def test_background_training_freezes_own_projects_annotation_root(tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", tmp_path / "empty_legacy")
    source, image = _source(tmp_path)
    second = source / "second.png"
    Image.new("RGB", (32, 24), "white").save(second)
    (source / "second.json").write_text(json.dumps({
        "imageWidth": 32, "imageHeight": 24,
        "shapes": [{"label": "Source", "shape_type": "rectangle", "points": [[1, 1], [5, 5]]}],
    }))
    captured = []

    def fake_start_job(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", fake_start_job)
    client = _client(tmp_path)
    first = client.post("/api/project/create", json={"name": "First train", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert _save(client, image, "First label").status_code == 200
    first_start = client.post("/api/training/start", json={"task": "segmentation", "dataset_path": str(source), "preset": "fast"})
    assert first_start.status_code == 200, first_start.text

    second_project = client.post("/api/project/create", json={"name": "Second train", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert _save(client, image, "Second label").status_code == 200
    assert second_project["id"] != first["id"]

    # The preparation callback runs after the request, while another project
    # is active. Its dataset must still use the first project's Studio labels.
    captured[0]["prepare_dataset"](threading.Event())
    rows = json.loads((Path(captured[0]["dataset_path"]) / "source_manifest.json").read_text())
    first_image_row = next(row for row in rows if row["source_image"] == str(image.resolve()))
    first_overlay = annotation_storage.dataset_annotation_dir(source, Path(first["annotations_dir"]), use_scope=False)
    frozen_label = Path(first_image_row["source_json"])
    assert frozen_label.is_relative_to(Path(captured[0]["output_dir"]) / "bound_annotations")
    assert frozen_label.name == "sample.json"
    assert frozen_label.read_bytes() == (first_overlay / "sample.json").read_bytes()
    assert json.loads(Path(first_image_row["source_json"]).read_text())["annotations"][0]["label"] == "First label"


def test_same_source_projects_keep_independent_saved_splits(tmp_path, monkeypatch):
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", tmp_path / "empty_legacy")
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "empty_legacy_splits")
    source, _ = _source(tmp_path)
    for number in range(2, 5):
        image = source / f"sample{number}.png"
        Image.new("RGB", (32, 24), "white").save(image)
        (source / f"sample{number}.json").write_text(json.dumps({
            "imageWidth": 32, "imageHeight": 24,
            "shapes": [{"label": "Source", "shape_type": "rectangle", "points": [[1, 1], [5, 5]]}],
        }))
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    client = _client(tmp_path)
    first = client.post("/api/project/create", json={"name": "First split", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    a_split = client.post("/api/dataset/split", json={"folder_path": str(source), "task": "segmentation", "train_ratio": 0.5, "seed": 11})
    assert a_split.status_code == 200, a_split.text
    a_manifest = Path(first["dataset_dir"]) / "splits" / f"{key}.json"
    a_bytes = a_manifest.read_bytes()

    second = client.post("/api/project/create", json={"name": "Second split", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    b_split = client.post("/api/dataset/split", json={"folder_path": str(source), "task": "segmentation", "train_ratio": 0.5, "seed": 99})
    assert b_split.status_code == 200, b_split.text
    b_manifest = Path(second["dataset_dir"]) / "splits" / f"{key}.json"
    assert b_manifest.is_file()
    assert b_manifest.read_bytes() != a_bytes
    assert a_manifest.read_bytes() == a_bytes
    assert not (tmp_path / "empty_legacy_splits" / f"{key}.json").exists()

    assert client.post("/api/project/open", json={"project_dir": first["project_dir"]}).status_code == 200
    assert client.post("/api/dataset/import", json={"folder_path": str(source), "task": "segmentation"}).status_code == 200
    assert a_manifest.read_bytes() == a_bytes


def test_global_saved_split_is_copied_without_changing_original(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy_splits"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", legacy_root)
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", tmp_path / "empty_legacy")
    source, _ = _source(tmp_path)
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    legacy_root.mkdir()
    legacy = legacy_root / f"{key}.json"
    payload = json.dumps({"folder_path": str(source.resolve()), "seed": 42, "assignments": {"sample.png": "train"}}).encode()
    legacy.write_bytes(payload)

    client = _client(tmp_path)
    project = client.post("/api/project/create", json={"name": "Migrated split", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    copied = Path(project["dataset_dir"]) / "splits" / f"{key}.json"
    assert copied.read_bytes() == payload
    assert legacy.read_bytes() == payload
    assert client.post("/api/project/open", json={"project_dir": project["project_dir"]}).status_code == 200
    assert copied.read_bytes() == payload
    # Restoring an older version with no split must not resurrect this legacy
    # manifest on a later project open.
    copied.unlink()
    assert client.post("/api/project/open", json={"project_dir": project["project_dir"]}).status_code == 200
    assert not copied.exists()
    assert legacy.read_bytes() == payload


def test_missing_legacy_split_is_not_imported_after_project_was_linked(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy_splits"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", legacy_root)
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", tmp_path / "empty_legacy")
    source, _ = _source(tmp_path)
    key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    client = _client(tmp_path)
    project = client.post("/api/project/create", json={"name": "No old split", "task": "segmentation"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    legacy_root.mkdir()
    legacy = legacy_root / f"{key}.json"
    legacy.write_text(json.dumps({"folder_path": str(source.resolve()), "assignments": {"sample.png": "train"}}))
    assert client.post("/api/project/open", json={"project_dir": project["project_dir"]}).status_code == 200
    assert not (Path(project["dataset_dir"]) / "splits" / f"{key}.json").exists()


def test_completed_legacy_checkpoint_survives_identical_overlay_copy_but_not_edit(tmp_path, monkeypatch):
    legacy_root = tmp_path / "legacy_annotations"
    legacy_splits = tmp_path / "legacy_splits"
    monkeypatch.setattr(annotation_storage, "LEGACY_ANNOTATIONS_ROOT", legacy_root)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", legacy_splits)
    source, _ = _source(tmp_path)
    legacy = annotation_storage.dataset_annotation_dir(source, legacy_root, use_scope=False)
    legacy.mkdir(parents=True)
    (legacy / "sample.json").write_text(json.dumps({
        "image_id": "sample", "annotations": [{"type": "bbox", "label": "Legacy", "bbox": [2, 2, 9, 10]}],
    }))
    baseline = fingerprint_dataset(source, studio_root=legacy_root)
    project_annotations = tmp_path / "project" / "annotations"
    copied = annotation_storage.dataset_annotation_dir(source, project_annotations, use_scope=False)
    shutil.copytree(legacy, copied)
    assert (legacy / "sample.json").read_bytes() == (copied / "sample.json").read_bytes()
    job = tmp_path / "model" / "job_12345_legacy"
    job.mkdir(parents=True)
    (job / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "detection", "source_dataset_path": str(source),
        "dataset_fingerprint": baseline,
    }))
    (job / "model_meta.json").write_text(json.dumps({"task": "detection"}))

    annotation_token = set_request_annotation_root(project_annotations)
    split_token = set_request_split_root(tmp_path / "project" / "dataset" / "splits")
    try:
        assert _matches_source_dataset(job, str(source), "detection")
        (copied / "sample.json").write_text(json.dumps({"annotations": []}))
        assert not _matches_source_dataset(job, str(source), "detection")
    finally:
        reset_request_split_root(split_token)
        reset_request_annotation_root(annotation_token)
