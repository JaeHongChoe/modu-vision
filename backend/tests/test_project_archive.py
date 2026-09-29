"""Project backups retain managed files and source bytes without overwriting input data."""

import hashlib
import json
import sqlite3
from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from backend.main import create_app
from backend.api import routes_dataset
from backend.engine.dataset_fingerprint import fingerprint_dataset


def _client(root: Path) -> TestClient:
    app = create_app(project_dir=str(root))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _project_with_model_flow_and_run(tmp_path: Path):
    client = _client(tmp_path / "projects")
    created = client.post("/api/project/create", json={"name": "Relocatable", "task": "segmentation"})
    assert created.status_code == 200, created.text
    project = created.json()
    source = tmp_path / "original_images"
    image = source / "test" / "part.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (24, 24), "white").save(image)
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200

    job_id = "job_123_archive"
    job_dir = Path(project["models_dir"]) / job_id
    (job_dir / "dataset").mkdir(parents=True)
    (job_dir / "dataset" / "prepared.txt").write_text("prepared training data", encoding="utf-8")
    (job_dir / "best_model.pt").write_bytes(b"completed checkpoint")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "segmentation"}), encoding="utf-8")
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "job_id": job_id, "status": "completed", "task": "segmentation",
        "source_dataset_path": str(source), "dataset_fingerprint": fingerprint,
        "dataset_path": str(job_dir / "dataset"), "output_dir": str(job_dir),
    }), encoding="utf-8")
    flow = client.get("/api/flowchart/templates/single-segmentation", params={"job_id": job_id})
    assert flow.status_code == 200, flow.text
    saved = client.post("/api/flowchart/pipeline", params={"source_dataset_path": str(source)}, json=flow.json())
    assert saved.status_code == 200, saved.text
    images = client.get("/api/dataset/images", params={
        "folder_path": str(source), "task": "segmentation",
    }).json()["items"]
    assert len(images) == 1
    run = client.post("/api/inspections/runs", json={
        "source_folder": str(source), "task": "segmentation", "scope": "all",
        "pipeline": flow.json(), "images": images,
    })
    assert run.status_code == 200, run.text
    return client, project, source, image, job_id, saved.json()["version_id"], run.json()["run_id"]


def test_archive_restores_source_and_studio_labels_to_new_workspace(tmp_path: Path):
    source = tmp_path / "original_images"
    source.mkdir()
    image = source / "part.png"
    Image.new("RGB", (24, 24), "white").save(image)
    source_bytes = image.read_bytes()
    client = _client(tmp_path / "projects")
    project = client.post("/api/project/create", json={"name": "Archive", "task": "detection"}).json()
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    saved = client.post("/api/annotations/save", json={
        "image_id": "part", "image_path": str(image), "image_width": 24,
        "image_height": 24,
        "annotations": [{"type": "bbox", "label": "Reviewed", "bbox": [1, 1, 12, 12]}],
    })
    assert saved.status_code == 200, saved.text
    output = tmp_path / "backups"
    output.mkdir()
    backed = client.post("/api/project/backup", json={"destination_dir": str(output)})
    assert backed.status_code == 200, backed.text
    archive = Path(backed.json()["archive_path"])
    assert archive.is_file() and backed.json()["source_included"] is True
    assert backed.json()["file_count"] >= 3

    target = tmp_path / "restored_project"
    restored = client.post("/api/project/restore", json={"archive_path": str(archive), "target_dir": str(target)})
    assert restored.status_code == 200, restored.text
    restored_project = restored.json()
    assert restored_project["project_dir"] == str(target)
    restored_image = Path(restored_project["source_dataset_dir"]) / "part.png"
    assert restored_image.read_bytes() == source_bytes
    assert image.read_bytes() == source_bytes
    viewed = client.get("/api/annotations/part", params={"file_path": str(restored_image)})
    assert viewed.status_code == 200, viewed.text
    assert [row["label"] for row in viewed.json()["annotations"]] == ["Reviewed"]
    assert Path(project["project_dir"]).is_dir()


def test_archive_rebinds_model_receipt_and_saved_flow_to_relocated_source(tmp_path: Path):
    client, project, source, _image, job_id, version_id, _run_id = _project_with_model_flow_and_run(tmp_path)
    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backups")})
    assert backed.status_code == 200, backed.text
    target = tmp_path / "restored_project"
    restored = client.post("/api/project/restore", json={
        "archive_path": backed.json()["archive_path"], "target_dir": str(target),
    })
    assert restored.status_code == 200, restored.text

    new_source = target / "dataset" / "restored_source"
    new_job = target / "models" / job_id
    receipt = json.loads((new_job / "job_receipt.json").read_text(encoding="utf-8"))
    assert receipt["source_dataset_path"] == str(new_source)
    assert receipt["dataset_path"] == str(new_job / "dataset")
    assert receipt["output_dir"] == str(new_job)
    verified = client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(new_source),
        "models": [{"job_id": job_id, "task": "segmentation"}],
    })
    assert verified.status_code == 200, verified.text
    active = client.get("/api/flowchart/pipeline/active")
    assert active.status_code == 200, active.text
    assert active.json()["nodes"]
    version = json.loads((target / "flowcharts" / "versions" / f"{version_id}.json").read_text(encoding="utf-8"))
    assert version["source_dataset_path"] == str(new_source)
    assert json.loads((target / "flowcharts" / "active.json").read_text(encoding="utf-8"))["source_dataset_path"] == str(new_source)
    assert json.loads((Path(project["models_dir"]) / job_id / "job_receipt.json").read_text(encoding="utf-8"))["source_dataset_path"] == str(source)


def test_archive_remaps_inspection_runs_and_keeps_reviews_in_their_project(tmp_path: Path):
    client, project, _source, image, job_id, _version_id, original_run_id = _project_with_model_flow_and_run(tmp_path)
    original_db = Path(project["project_dir"]) / "inspection_history.sqlite3"
    with sqlite3.connect(original_db) as conn:
        conn.execute("UPDATE rows SET state = 'REVIEW' WHERE run_id = ?", (original_run_id,))
    reviewed = client.post(f"/api/inspections/runs/{original_run_id}/reviews", json={
        "image_path": str(image), "final_verdict": "NG", "reviewer": "operator", "reason": "Original inspection",
    })
    assert reviewed.status_code == 200, reviewed.text

    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backups")})
    assert backed.status_code == 200, backed.text
    target = tmp_path / "restored_project"
    restored = client.post("/api/project/restore", json={
        "archive_path": backed.json()["archive_path"], "target_dir": str(target),
    })
    assert restored.status_code == 200, restored.text
    listed = client.get("/api/inspections/runs")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["runs"]) == 1
    restored_run_id = listed.json()["runs"][0]["run_id"]
    assert restored_run_id != original_run_id
    assert listed.json()["runs"][0]["source_folder"] == str(target / "dataset" / "restored_source")
    restored_image = target / "dataset" / "restored_source" / "test" / "part.png"
    restored_run = client.get(f"/api/inspections/runs/{restored_run_id}")
    assert restored_run.status_code == 200, restored_run.text
    assert restored_run.json()["rows"][0]["image"]["file_path"] == str(restored_image)
    assert restored_run.json()["rows"][0]["reviews"][0]["reviewer"] == "operator"
    with sqlite3.connect(target / "inspection_history.sqlite3") as conn:
        recorded = json.loads(conn.execute(
            "SELECT model_paths_json FROM runs WHERE run_id = ?", (restored_run_id,),
        ).fetchone()[0])
    assert recorded[job_id] == str(target / "models" / job_id / "best_model.pt")

    second_review = client.post(f"/api/inspections/runs/{restored_run_id}/reviews", json={
        "image_path": str(restored_image), "final_verdict": "OK", "reviewer": "restored operator", "reason": "Retested restored image",
    })
    assert second_review.status_code == 200, second_review.text
    assert len(client.get(f"/api/inspections/runs/{restored_run_id}").json()["rows"][0]["reviews"]) == 2
    with sqlite3.connect(original_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews WHERE run_id = ?", (original_run_id,)).fetchone()[0] == 1
    opened = client.post("/api/project/open", json={"project_dir": project["project_dir"]})
    assert opened.status_code == 200, opened.text
    assert client.get(f"/api/inspections/runs/{original_run_id}").json()["rows"][0]["image"]["file_path"] == str(image)
    assert len(client.get(f"/api/inspections/runs/{restored_run_id}").json()["rows"][0]["reviews"]) == 2


def test_archive_rebinds_split_filename_and_folder_path(tmp_path: Path):
    client = _client(tmp_path / "projects")
    created = client.post("/api/project/create", json={"name": "Split", "task": "segmentation"})
    assert created.status_code == 200, created.text
    project = created.json()
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (8, 8), "white").save(source / "part.png")
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    original_key = hashlib.sha256(str(source).encode()).hexdigest()
    original_split = Path(project["dataset_dir"]) / "splits" / f"{original_key}.json"
    original_split.parent.mkdir(parents=True, exist_ok=True)
    original_split.write_text(json.dumps({
        "folder_path": str(source), "seed": 7, "assignments": {"part.png": "test"},
    }), encoding="utf-8")

    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backups")})
    assert backed.status_code == 200, backed.text
    target = tmp_path / "restored_project"
    restored = client.post("/api/project/restore", json={
        "archive_path": backed.json()["archive_path"], "target_dir": str(target),
    })
    assert restored.status_code == 200, restored.text
    new_source = target / "dataset" / "restored_source"
    new_key = hashlib.sha256(str(new_source).encode()).hexdigest()
    new_split = target / "dataset" / "splits" / f"{new_key}.json"
    assert json.loads(new_split.read_text(encoding="utf-8"))["folder_path"] == str(new_source)
    assert json.loads(original_split.read_text(encoding="utf-8"))["folder_path"] == str(source)


def test_archive_preserves_unrelated_json_bytes_even_if_invalid(tmp_path: Path):
    client = _client(tmp_path / "projects")
    project = client.post("/api/project/create", json={"name": "Notes"}).json()
    note = Path(project["project_dir"]) / "notes.json"
    note.write_bytes(b"{private note bytes are not JSON}")
    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backups")})
    assert backed.status_code == 200, backed.text
    target = tmp_path / "restored_project"
    restored = client.post("/api/project/restore", json={
        "archive_path": backed.json()["archive_path"], "target_dir": str(target),
    })
    assert restored.status_code == 200, restored.text
    assert (target / "notes.json").read_bytes() == note.read_bytes()


def test_archive_restore_rejects_corrupt_member_and_existing_destination(tmp_path: Path):
    client = _client(tmp_path / "projects")
    client.post("/api/project/create", json={"name": "Archive"})
    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path)}).json()
    archive = Path(backed["archive_path"])
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    assert client.post("/api/project/restore", json={"archive_path": str(archive), "target_dir": str(occupied)}).status_code == 409

    tampered = tmp_path / "tampered.zip"
    with ZipFile(archive) as original, ZipFile(tampered, "w") as output:
        for item in original.infolist():
            data = original.read(item.filename)
            output.writestr(item.filename, b"changed" if item.filename == "project/project.json" else data)
    target = tmp_path / "empty"
    rejected = client.post("/api/project/restore", json={"archive_path": str(tampered), "target_dir": str(target)})
    assert rejected.status_code == 422, rejected.text
    assert not target.exists()


def test_archive_restores_active_labelset_and_version_verification(tmp_path: Path):
    source = tmp_path / "original_images"
    source.mkdir()
    image = source / "part.png"
    Image.new("RGB", (24, 24), "white").save(image)
    client = _client(tmp_path / "projects")
    client.post("/api/project/create", json={"name": "Archive", "task": "detection"})
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    second_id = client.post("/api/project/labelsets", json={"name": "Review B"}).json()["id"]
    client.put(f"/api/project/labelsets/{second_id}/activate")
    saved = client.post("/api/annotations/save", json={
        "image_id": "part", "image_path": str(image), "image_width": 24,
        "image_height": 24,
        "annotations": [{"type": "bbox", "label": "Review B", "bbox": [1, 1, 12, 12]}],
    })
    assert saved.status_code == 200, saved.text
    version = client.post("/api/dataset/versions", json={"name": "Reviewed"})
    assert version.status_code == 200, version.text
    backed = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "output")})
    assert backed.status_code == 200, backed.text
    restored = client.post("/api/project/restore", json={
        "archive_path": backed.json()["archive_path"], "target_dir": str(tmp_path / "new_project"),
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["active_labelset_id"] == second_id
    restored_image = Path(restored.json()["source_dataset_dir"]) / "part.png"
    viewed = client.get("/api/annotations/part", params={"file_path": str(restored_image)})
    assert [row["label"] for row in viewed.json()["annotations"]] == ["Review B"]
    verified = client.get(f"/api/dataset/versions/{version.json()['id']}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"


def test_archive_rejects_dataset_above_size_limit(tmp_path: Path, monkeypatch):
    from backend.engine import project_archive
    monkeypatch.setattr(project_archive, "MAX_SOURCE_BYTES", 10)
    source = tmp_path / "images"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"x" * 20)
    client = _client(tmp_path / "projects")
    client.post("/api/project/create", json={"name": "Large"})
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    response = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "output")})
    assert response.status_code == 413
    assert not (tmp_path / "output").exists()


def test_restore_rejects_new_folder_inside_original_source(tmp_path: Path):
    source = tmp_path / "images"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"image")
    client = _client(tmp_path / "projects")
    client.post("/api/project/create", json={"name": "Archive"})
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    archive = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backup")}).json()["archive_path"]
    target = source / "nested_project"
    response = client.post("/api/project/restore", json={"archive_path": archive, "target_dir": str(target)})
    assert response.status_code == 422
    assert not target.exists()
