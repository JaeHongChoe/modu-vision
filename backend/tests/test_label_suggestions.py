"""Completed models propose labels for review without changing source data."""

import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.api import routes_annotation, routes_dataset, routes_dataset_versions, routes_project
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.dataset_loaders import reset_request_split_root, set_request_split_root


_original_httpx_init = httpx.Client.__init__


def _compatible_httpx_init(self, *args, app=None, **kwargs):
    return _original_httpx_init(self, *args, **kwargs)


httpx.Client.__init__ = _compatible_httpx_init


@pytest.fixture
def suggestion_workspace(tmp_path: Path, monkeypatch):
    from backend.api import routes_label_suggestions

    annotation_root = tmp_path / "studio_annotations"
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", annotation_root)
    monkeypatch.setattr(routes_annotation, "ANNOTATIONS_DIR", annotation_root)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "splits")
    source = tmp_path / "source"
    source.mkdir()
    image = source / "first.png"
    Image.new("RGB", (64, 48), "white").save(image)
    labelme = source / "first.json"
    labelme.write_text(json.dumps({
        "imageWidth": 64, "imageHeight": 48,
        "shapes": [{"label": "original", "shape_type": "rectangle", "points": [[2, 3], [8, 9]]}],
    }))
    app = FastAPI()
    app.state.project_dir = tmp_path / "projects"

    @app.middleware("http")
    async def project_storage_scope(request, call_next):
        active = getattr(app.state, "current_project", None)
        if active is None:
            return await call_next(request)
        annotation_token = set_request_annotation_root(Path(active["annotations_dir"]))
        split_token = set_request_split_root(Path(active["dataset_dir"]) / "splits")
        project_token = set_request_project_root(Path(active["project_dir"]))
        try:
            return await call_next(request)
        finally:
            reset_request_project_root(project_token)
            reset_request_split_root(split_token)
            reset_request_annotation_root(annotation_token)

    app.include_router(routes_project.router)
    app.include_router(routes_dataset_versions.router)
    app.include_router(routes_label_suggestions.router)
    client = TestClient(app)
    project = client.post("/api/project/create", json={"name": "Ceramic", "task": "detection"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    model_dir = Path(project["models_dir"]) / "job_12345_test"
    model_dir.mkdir(parents=True)
    checkpoint = model_dir / "best_model.pt"
    checkpoint.write_bytes(b"fake completed checkpoint")
    (model_dir / "model_meta.json").write_text(json.dumps({"task": "detection", "classes": ["background", "defect"]}))
    (model_dir / "job_receipt.json").write_text(json.dumps({
        "job_id": model_dir.name, "status": "completed", "task": "detection",
        "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint_dataset(source, studio_root=Path(project["annotations_dir"]), use_scope=False),
    }))
    studio = dataset_annotation_dir(source, Path(project["annotations_dir"]), use_scope=False)

    def fake_infer(task, model_path, image_input, threshold=0.5, device=None):
        assert task == "detection"
        assert Path(model_path) == checkpoint
        assert Path(image_input) == image
        return SimpleNamespace(predictions=[
            {"bbox": [10, 10, 20, 20], "score": 0.91, "label": "defect"},
            {"bbox": [30, 11, 40, 21], "score": 0.72, "label": "defect"},
        ], confidence_score=0.91, latency_ms=13.2)

    monkeypatch.setattr(routes_label_suggestions, "infer", fake_infer)
    return client, project, source, image, labelme, studio, checkpoint


def test_generate_persists_pending_proposal_and_never_edits_labels(suggestion_workspace):
    client, project, _, image, labelme, studio, checkpoint = suggestion_workspace
    original = labelme.read_bytes()
    models = client.get("/api/label-suggestions/models")
    assert models.status_code == 200, models.text
    assert [item["job_id"] for item in models.json()["models"]] == ["job_12345_test"]

    result = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(image), "threshold": 0.5,
    })
    assert result.status_code == 200, result.text
    proposal = result.json()
    assert proposal["status"] == "pending"
    assert len(proposal["candidates"]) == 2
    assert proposal["image_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert proposal["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert labelme.read_bytes() == original
    assert not list(studio.glob("*.json"))  # Identity ledger can exist without editable labels.
    assert (Path(project["project_dir"]) / "label_suggestions" / f"{proposal['id']}.json").is_file()
    assert client.get(f"/api/label-suggestions/{proposal['id']}").json()["id"] == proposal["id"]
    assert len(client.get("/api/label-suggestions", params={"image_path": str(image)}).json()["suggestions"]) == 1


def test_accept_selected_candidate_snapshots_first_and_preserves_labelme(suggestion_workspace):
    client, project, _, image, labelme, studio, _ = suggestion_workspace
    original = labelme.read_bytes()
    proposal = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(image),
    }).json()
    chosen = proposal["candidates"][0]["id"]
    reviewed = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={
        "decision": "accept", "candidate_ids": [chosen],
    })
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "accepted"
    assert reviewed.json()["backup_version_id"].startswith("v_")
    saved = json.loads((studio / "first.json").read_text())
    assert len(saved["annotations"]) == 2
    assert saved["annotations"][0]["label"] == "original"
    assert saved["annotations"][1]["id"] == chosen
    assert labelme.read_bytes() == original
    assert client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "accept", "candidate_ids": [chosen]}).status_code == 409
    version = client.get(f"/api/dataset/versions/{reviewed.json()['backup_version_id']}").json()
    assert version["kind"] == "auto_backup"
    assert not any(row["origin"] == "studio" and not row["relative_path"].startswith("metadata/") for row in version["files"])
    assert any(row["relative_path"] == "metadata/workflow.json" for row in version["files"])
    assert Path(project["project_dir"]).is_dir()


def test_reject_keeps_source_and_overlay_unmodified(suggestion_workspace):
    client, _, _, image, labelme, studio, _ = suggestion_workspace
    original = labelme.read_bytes()
    proposal = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(image),
    }).json()
    reviewed = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "reject"})
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "rejected"
    assert labelme.read_bytes() == original
    assert not list(studio.glob("*.json"))  # Identity ledger can exist without editable labels.


def test_changed_image_or_checkpoint_blocks_accept_without_edit(suggestion_workspace):
    client, _, _, image, _, studio, checkpoint = suggestion_workspace
    proposal = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(image),
    }).json()
    checkpoint.write_bytes(b"changed checkpoint")
    rejected = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={
        "decision": "accept", "candidate_ids": [proposal["candidates"][0]["id"]],
    })
    assert rejected.status_code == 409
    assert not list(studio.glob("*.json"))  # Identity ledger can exist without editable labels.
    assert client.get(f"/api/label-suggestions/{proposal['id']}").json()["status"] == "pending"


def test_changed_other_image_label_blocks_accept_without_edit(suggestion_workspace):
    client, project, source, image, labelme, studio, _ = suggestion_workspace
    other = source / "other.png"
    Image.new("RGB", (64, 48), "white").save(other)
    other_label = source / "other.json"
    other_label.write_text(json.dumps({
        "imageWidth": 64, "imageHeight": 48,
        "shapes": [{"label": "old", "shape_type": "rectangle", "points": [[1, 1], [5, 5]]}],
    }))
    _refresh_receipt_fingerprint(project, source)
    proposal = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(image),
    }).json()
    original_labelme = labelme.read_bytes()
    other_label.write_text(other_label.read_text().replace('"old"', '"changed"'))

    rejected = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={
        "decision": "accept", "candidate_ids": [proposal["candidates"][0]["id"]],
    })
    assert rejected.status_code == 409, rejected.text
    assert "fingerprint" in rejected.json()["detail"]
    assert labelme.read_bytes() == original_labelme
    assert not list(studio.glob("*.json"))  # Identity ledger can exist without editable labels.
    assert client.get(f"/api/label-suggestions/{proposal['id']}").json()["status"] == "pending"


def test_model_catalog_requires_same_source_task_and_completed_receipt(suggestion_workspace):
    client, project, source, _, _, _, _ = suggestion_workspace
    models_root = Path(project["models_dir"])
    for suffix, task, status, path in [
        ("wrong_task", "segmentation", "completed", str(source)),
        ("running", "detection", "running", str(source)),
        ("other_source", "detection", "completed", str(source / "other")),
    ]:
        folder = models_root / f"job_12345_{suffix}"
        folder.mkdir()
        (folder / "best_model.pt").write_bytes(b"fake")
        (folder / "model_meta.json").write_text(json.dumps({"task": task}))
        (folder / "job_receipt.json").write_text(json.dumps({
            "job_id": folder.name, "status": status, "task": task, "source_dataset_path": path,
        }))
    models = client.get("/api/label-suggestions/models")
    assert models.status_code == 200
    assert [item["job_id"] for item in models.json()["models"]] == ["job_12345_test"]


def test_path_traversal_cannot_infer_image_outside_imported_source(suggestion_workspace, tmp_path):
    client, _, source, _, _, _, _ = suggestion_workspace
    outside = tmp_path / "outside.png"
    Image.new("RGB", (12, 12), "black").save(outside)
    disguised = str(source / ".." / outside.name)
    result = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": disguised,
    })
    assert result.status_code == 422


def _refresh_receipt_fingerprint(project: dict, source: Path) -> None:
    receipt_path = Path(project["models_dir"]) / "job_12345_test" / "job_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["dataset_fingerprint"] = fingerprint_dataset(
        source, studio_root=Path(project["annotations_dir"]), use_scope=False,
    )
    receipt_path.write_text(json.dumps(receipt))


def _wait_for_batch(client: TestClient, batch_id: str, timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/label-suggestions/batches/{batch_id}")
        assert response.status_code == 200, response.text
        batch = response.json()
        if batch["status"] in {"completed", "cancelled", "failed", "interrupted"}:
            return batch
        time.sleep(0.02)
    raise AssertionError("Batch did not reach a terminal state")


def test_bulk_proposals_persist_per_image_outcomes_without_auto_accept(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, first, labelme, studio, _ = suggestion_workspace
    second = source / "second.png"
    third = source / "third.png"
    Image.new("RGB", (64, 48), "white").save(second)
    Image.new("RGB", (64, 48), "white").save(third)
    _refresh_receipt_fingerprint(project, source)
    labelme_before = labelme.read_bytes()

    def varied_infer(task, model_path, image_input, threshold=0.5, device=None):
        if Path(image_input) == third:
            raise RuntimeError("unreadable sample")
        predictions = [] if Path(image_input) == second else [{"bbox": [10, 10, 20, 20], "score": 0.9, "label": "defect"}]
        return SimpleNamespace(predictions=predictions, confidence_score=0.9, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", varied_infer)
    started = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "threshold": 0.5,
        "image_paths": [str(first), str(second), str(third)],
    })
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    assert (batch["total"], batch["processed"], batch["generated"], batch["zero_candidates"], batch["failed"]) == (3, 3, 1, 1, 1)
    assert [item["status"] for item in batch["entries"]] == ["generated", "zero_candidates", "failed"]
    assert "unreadable sample" in batch["entries"][2]["error"]
    proposal_id = batch["entries"][0]["proposal_id"]
    assert client.get(f"/api/label-suggestions/{proposal_id}").json()["status"] == "pending"
    assert labelme.read_bytes() == labelme_before
    assert not list(studio.glob("*.json"))  # Identity ledger can exist without editable labels.
    assert any(item["id"] == batch["id"] for item in client.get("/api/label-suggestions/batches").json()["batches"])
    assert (Path(project["project_dir"]) / "label_suggestions" / "batches" / f"{batch['id']}.json").is_file()

    other = client.post("/api/project/create", json={"name": "Other", "task": "detection"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert client.get("/api/label-suggestions/batches").json()["batches"] == []
    assert client.get("/api/label-suggestions").json()["suggestions"] == []
    assert other["id"] != project["id"]


def test_bulk_defaults_to_only_unlabeled_images(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, _, _, _, _ = suggestion_workspace
    unlabeled = source / "unlabeled.png"
    Image.new("RGB", (64, 48), "white").save(unlabeled)
    _refresh_receipt_fingerprint(project, source)
    observed: list[Path] = []

    def record_infer(task, model_path, image_input, threshold=0.5, device=None):
        observed.append(Path(image_input))
        return SimpleNamespace(predictions=[], confidence_score=0, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", record_infer)
    started = client.post("/api/label-suggestions/batches", json={"job_id": "job_12345_test"})
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    assert batch["total"] == 1
    assert batch["entries"][0]["image_path"] == str(unlabeled)
    assert observed == [unlabeled]


def test_bulk_unlabeled_selection_matches_stage1_when_studio_mask_is_missing(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, image, _, studio, _ = suggestion_workspace
    studio.mkdir(parents=True)
    (studio / "first.json").write_text(json.dumps({
        "image_id": "first", "annotations": [], "mask_file": str(studio / "masks" / "first.png"),
    }))
    _refresh_receipt_fingerprint(project, source)
    token = set_request_annotation_root(Path(project["annotations_dir"]))
    try:
        assert image.resolve() not in routes_dataset._paired_labelme_images(source)
    finally:
        reset_request_annotation_root(token)
    monkeypatch.setattr(routes_label_suggestions, "infer", lambda **kwargs: SimpleNamespace(
        predictions=[], confidence_score=0, latency_ms=1.0,
    ))
    started = client.post("/api/label-suggestions/batches", json={"job_id": "job_12345_test"})
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    assert [entry["image_path"] for entry in batch["entries"]] == [str(image)]


def test_studio_ng_tag_is_labeled_in_stage1_and_excluded_from_default_batch(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, _, _, _, _ = suggestion_workspace
    tagged = source / "tagged.png"
    unlabeled = source / "unlabeled.png"
    Image.new("RGB", (64, 48), "white").save(tagged)
    Image.new("RGB", (64, 48), "white").save(unlabeled)
    studio = dataset_annotation_dir(source, Path(project["annotations_dir"]), use_scope=False)
    studio.mkdir(parents=True)
    (studio / "tagged.json").write_text(json.dumps({
        "image_id": "tagged", "annotations": [{"type": "tag", "label": "NG", "is_normal": False}],
    }))
    _refresh_receipt_fingerprint(project, source)
    token = set_request_annotation_root(Path(project["annotations_dir"]))
    try:
        assert tagged.resolve() in routes_dataset._paired_labelme_images(source)
    finally:
        reset_request_annotation_root(token)
    monkeypatch.setattr(routes_label_suggestions, "infer", lambda **kwargs: SimpleNamespace(
        predictions=[], confidence_score=0, latency_ms=1.0,
    ))
    started = client.post("/api/label-suggestions/batches", json={"job_id": "job_12345_test"})
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    assert [entry["image_path"] for entry in batch["entries"]] == [str(unlabeled)]


def test_bulk_cancel_finishes_current_image_and_skips_remaining(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, first, _, _, _ = suggestion_workspace
    second = source / "second.png"
    Image.new("RGB", (64, 48), "white").save(second)
    _refresh_receipt_fingerprint(project, source)
    prior = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(first),
    }).json()
    entered = threading.Event()
    release = threading.Event()
    observed: list[Path] = []

    def blocking_infer(task, model_path, image_input, threshold=0.5, device=None):
        observed.append(Path(image_input))
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(predictions=[], confidence_score=0, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", blocking_infer)
    started = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(first), str(second)],
    })
    assert started.status_code == 200, started.text
    assert entered.wait(2)
    blocked_accept = client.post(f"/api/label-suggestions/{prior['id']}/review", json={
        "decision": "accept", "candidate_ids": [prior["candidates"][0]["id"]],
    })
    assert blocked_accept.status_code == 409, blocked_accept.text
    cancelled = client.post(f"/api/label-suggestions/batches/{started.json()['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelling"
    release.set()
    batch = _wait_for_batch(client, started.json()["id"])
    assert batch["status"] == "cancelled"
    assert batch["processed"] == 1
    assert [item["status"] for item in batch["entries"]] == ["zero_candidates", "queued"]
    assert observed == [first]


def test_changed_training_fingerprint_blocks_new_single_and_bulk_proposals(suggestion_workspace):
    client, _, _, image, labelme, studio, _ = suggestion_workspace
    labelme.write_text(labelme.read_text() + " ")
    assert client.get("/api/label-suggestions/models").json()["models"] == []
    single = client.post("/api/label-suggestions/generate", json={
        "job_id": "job_12345_test", "image_path": str(image),
    })
    bulk = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(image)],
    })
    assert single.status_code == 409, single.text
    assert bulk.status_code == 409, bulk.text
    assert not list(studio.glob("*.json"))  # Identity ledger can exist without editable labels.


def test_pending_batch_proposals_remain_reviewable_after_first_accept(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, first, labelme, studio, _ = suggestion_workspace
    second = source / "second.png"
    Image.new("RGB", (64, 48), "white").save(second)
    _refresh_receipt_fingerprint(project, source)
    original_labelme = labelme.read_bytes()

    def one_candidate(task, model_path, image_input, threshold=0.5, device=None):
        return SimpleNamespace(predictions=[{"bbox": [10, 10, 20, 20], "score": 0.9, "label": "defect"}],
                               confidence_score=0.9, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", one_candidate)
    started = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(first), str(second)],
    })
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    assert batch["generated"] == 2
    for entry in batch["entries"]:
        proposal = client.get(f"/api/label-suggestions/{entry['proposal_id']}").json()
        reviewed = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={
            "decision": "accept", "candidate_ids": [proposal["candidates"][0]["id"]],
        })
        assert reviewed.status_code == 200, reviewed.text
    assert labelme.read_bytes() == original_labelme
    assert (studio / "first.json").is_file()
    assert dataset_annotation_dir(source, Path(project["annotations_dir"]), use_scope=False).joinpath("second.json").is_file()


@pytest.mark.parametrize("external_change", ["other_label", "split"])
def test_external_dataset_change_between_batch_reviews_blocks_next_accept(
        suggestion_workspace, monkeypatch, external_change):
    from backend.api import routes_label_suggestions

    client, project, source, first, labelme, studio, _ = suggestion_workspace
    second = source / "second.png"
    third = source / "third.png"
    Image.new("RGB", (64, 48), "white").save(second)
    Image.new("RGB", (64, 48), "white").save(third)
    _refresh_receipt_fingerprint(project, source)
    monkeypatch.setattr(routes_label_suggestions, "infer", lambda **kwargs: SimpleNamespace(
        predictions=[{"bbox": [1, 1, 5, 5], "score": 0.9, "label": "defect"}],
        confidence_score=0.9, latency_ms=1.0,
    ))
    started = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(first), str(second)],
    })
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    assert batch["generated"] == 2
    first_proposal = client.get(f"/api/label-suggestions/{batch['entries'][0]['proposal_id']}").json()
    second_proposal = client.get(f"/api/label-suggestions/{batch['entries'][1]['proposal_id']}").json()
    accepted = client.post(f"/api/label-suggestions/{first_proposal['id']}/review", json={
        "decision": "accept", "candidate_ids": [first_proposal["candidates"][0]["id"]],
    })
    assert accepted.status_code == 200, accepted.text

    if external_change == "other_label":
        (source / "third.json").write_text(json.dumps({
            "imageWidth": 64, "imageHeight": 48,
            "shapes": [{"label": "manual", "shape_type": "rectangle", "points": [[2, 2], [8, 8]]}],
        }))
    else:
        split_root = Path(project["dataset_dir"]) / "splits"
        split_root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
        (split_root / f"{key}.json").write_text(json.dumps({"train": [str(first)], "val": [str(second)]}))
    original_labelme = labelme.read_bytes()
    rejected = client.post(f"/api/label-suggestions/{second_proposal['id']}/review", json={
        "decision": "accept", "candidate_ids": [second_proposal["candidates"][0]["id"]],
    })
    assert rejected.status_code == 409, rejected.text
    assert "fingerprint" in rejected.json()["detail"]
    assert labelme.read_bytes() == original_labelme
    assert not (studio / "second.json").exists()


def test_reopen_marks_orphaned_running_batch_interrupted(suggestion_workspace):
    client, project, source, _, _, _, _ = suggestion_workspace
    root = Path(project["project_dir"]) / "label_suggestions" / "batches"
    root.mkdir(parents=True)
    batch_id = "batch_" + "a" * 24
    (root / f"{batch_id}.json").write_text(json.dumps({
        "id": batch_id, "project_id": project["id"], "source_dataset_dir": str(source),
        "status": "running", "total": 2, "processed": 1, "entries": [
            {"image_path": str(source / "first.png"), "status": "generated", "proposal_id": "saved"},
            {"image_path": str(source / "other.png"), "status": "queued"},
        ],
    }))
    reopened = client.get(f"/api/label-suggestions/batches/{batch_id}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "interrupted"
    assert reopened.json()["processed"] == 1
    assert json.loads((root / f"{batch_id}.json").read_text())["status"] == "interrupted"


def test_source_relink_stops_old_batch_and_hides_old_proposals(suggestion_workspace, monkeypatch, tmp_path):
    from backend.api import routes_label_suggestions

    client, project, source, first, _, _, _ = suggestion_workspace
    second = source / "second.png"
    Image.new("RGB", (64, 48), "white").save(second)
    _refresh_receipt_fingerprint(project, source)
    entered = threading.Event()
    release = threading.Event()

    def blocking_infer(task, model_path, image_input, threshold=0.5, device=None):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(predictions=[], confidence_score=0, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", blocking_infer)
    started = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(first), str(second)],
    })
    assert started.status_code == 200, started.text
    assert entered.wait(2)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    Image.new("RGB", (64, 48), "white").save(replacement / "new.png")
    assert client.put("/api/project/update", json={"source_dataset_dir": str(replacement)}).status_code == 200
    release.set()
    # The old batch file remains readable as an artifact, while active source
    # listings must not include its proposals.
    deadline = time.monotonic() + 5
    old_batch = Path(project["project_dir"]) / "label_suggestions" / "batches" / f"{started.json()['id']}.json"
    while time.monotonic() < deadline:
        saved = json.loads(old_batch.read_text())
        if saved["status"] in {"failed", "completed", "cancelled"}:
            break
        time.sleep(0.02)
    assert saved["status"] == "failed"
    assert saved["entries"][1]["status"] == "failed"
    assert client.get("/api/label-suggestions").json()["suggestions"] == []


def test_task_change_stops_old_batch_before_saving_or_inferencing_more(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, first, _, _, _ = suggestion_workspace
    second = source / "second.png"
    Image.new("RGB", (64, 48), "white").save(second)
    _refresh_receipt_fingerprint(project, source)
    entered = threading.Event()
    release = threading.Event()
    observed: list[Path] = []

    def blocking_infer(task, model_path, image_input, threshold=0.5, device=None):
        observed.append(Path(image_input))
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(predictions=[{"bbox": [1, 1, 5, 5], "score": 0.9, "label": "defect"}],
                               confidence_score=0.9, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", blocking_infer)
    started = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(first), str(second)],
    })
    assert started.status_code == 200, started.text
    assert entered.wait(2)
    assert client.put("/api/project/update", json={"task": "segmentation"}).status_code == 200
    release.set()
    batch = _wait_for_batch(client, started.json()["id"])
    assert batch["status"] == "failed"
    assert [entry["status"] for entry in batch["entries"]] == ["failed", "failed"]
    assert observed == [first]
    assert client.get("/api/label-suggestions").json()["suggestions"] == []


@pytest.mark.parametrize("layout,task", [
    ("coco", "detection"),
    ("yolo", "detection"),
    ("classification", "classification"),
])
def test_default_batch_rejects_structured_labels_but_explicit_selection_remains_available(
        suggestion_workspace, monkeypatch, tmp_path, layout, task):
    from backend.api import routes_label_suggestions

    client, project, _, _, _, _, _ = suggestion_workspace
    source = tmp_path / layout
    if layout == "coco":
        image = source / "images" / "part.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (64, 48), "white").save(image)
        annotations = source / "annotations"
        annotations.mkdir()
        (annotations / "instances.json").write_text(json.dumps({
            "images": [{"id": 1, "file_name": "part.png"}],
            "annotations": [{"image_id": 1, "category_id": 1, "bbox": [2, 3, 6, 7]}],
            "categories": [{"id": 1, "name": "defect"}],
        }))
    elif layout == "yolo":
        image = source / "images" / "train" / "part.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (64, 48), "white").save(image)
        labels = source / "labels" / "train"
        labels.mkdir(parents=True)
        (labels / "part.txt").write_text("0 0.5 0.5 0.25 0.25\n")
        (source / "dataset.yaml").write_text("path: .\ntrain: images/train\nnames: [defect]\n")
    else:
        image = source / "train" / "OK" / "part.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (64, 48), "white").save(image)
    updated = client.put("/api/project/update", json={
        "task": task, "source_dataset_dir": str(source),
    })
    assert updated.status_code == 200, updated.text
    model_dir = Path(project["models_dir"]) / "job_12345_test"
    meta = json.loads((model_dir / "model_meta.json").read_text())
    meta["task"] = task
    (model_dir / "model_meta.json").write_text(json.dumps(meta))
    receipt = json.loads((model_dir / "job_receipt.json").read_text())
    receipt.update(task=task, source_dataset_path=str(source), dataset_fingerprint=fingerprint_dataset(
        source, studio_root=Path(project["annotations_dir"]), use_scope=False,
    ))
    (model_dir / "job_receipt.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(routes_label_suggestions, "infer", lambda **kwargs: SimpleNamespace(
        predictions=[], confidence_score=0.0, latency_ms=1.0,
    ))

    automatic = client.post("/api/label-suggestions/batches", json={"job_id": "job_12345_test"})
    assert automatic.status_code == 422, automatic.text
    assert "flat LabelMe" in automatic.json()["detail"]
    assert client.get("/api/label-suggestions/batches").json()["batches"] == []

    explicit = client.post("/api/label-suggestions/batches", json={
        "job_id": "job_12345_test", "image_paths": [str(image)],
    })
    assert explicit.status_code == 200, explicit.text
    assert _wait_for_batch(client, explicit.json()["id"])["total"] == 1
