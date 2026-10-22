"""Adopted machine drafts remember where they came from, and one adoption can be taken back exactly.

Drafts are proposals until a person adopts them. An adopted shape keeps an origin (draft, model, reviewer, backup).
Taking back an adoption removes only the shapes it added, backs the labels up first, and refuses when anyone changed
those shapes since, pointing to the data version taken before the adoption instead.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from backend.api import routes_annotation
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.tests.test_label_suggestions import (  # noqa: F401  (pytest fixture)
    _refresh_receipt_fingerprint, _wait_for_batch, suggestion_workspace,
)


def _adopt(client, image, index=0):
    proposal = client.post("/api/label-suggestions/generate", json={"job_id": "job_12345_test", "image_path": str(image)}).json()
    chosen = proposal["candidates"][index]["id"]
    reviewed = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "accept", "candidate_ids": [chosen]})
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json(), chosen


def _labels(studio, name="first"):
    return json.loads((studio / f"{name}.json").read_text())["annotations"]


def _person_save(client, image, annotations):
    client.app.include_router(routes_annotation.router)
    current = client.get(f"/api/annotations/{image.stem}", params={"file_path": str(image)}).json()
    saved = client.post("/api/annotations/save", json={
        "image_id": image.stem, "image_path": str(image), "image_width": 64, "image_height": 48,
        "annotations": annotations(current["annotations"]), "expected_revision": current["metadata"]["revision"], "actor": "person-b"})
    assert saved.status_code == 200, saved.text


def test_adopted_shape_records_its_origin_and_the_proposal_keeps_the_saved_copy(suggestion_workspace):
    client, _, _, image, labelme, studio, checkpoint = suggestion_workspace
    original = labelme.read_bytes()
    proposal, chosen = _adopt(client, image)
    labels = _labels(studio)
    assert "origin" not in labels[0], 'a shape a person drew has no origin key'
    adopted = labels[1]
    assert adopted["id"] == chosen
    origin = adopted["origin"]
    assert origin["kind"] == "model_draft" and origin["proposal_id"] == proposal["id"] and origin["candidate_id"] == chosen
    assert origin["backend"] == "trained_model" and origin["job_id"] == "job_12345_test"
    assert origin["checkpoint_sha256"] == proposal["checkpoint_sha256"]
    assert origin["reviewer"] == "operator" and origin["adopted_at"].endswith("Z")
    assert origin["backup_version_id"] == proposal["backup_version_id"]
    assert proposal["adopted_annotations"] == [adopted]
    assert labelme.read_bytes() == original


def test_taking_back_an_adoption_removes_only_its_shapes_keeps_a_later_person_edit_and_backs_up_first(suggestion_workspace):
    client, _, _, image, labelme, studio, _ = suggestion_workspace
    original = labelme.read_bytes()
    proposal, chosen = _adopt(client, image)
    person = {"id": "person-shape", "type": "bbox", "label": "scratch", "bbox": [40, 30, 50, 40]}
    _person_save(client, image, lambda current: [*current, person])
    before = _labels(studio)
    assert [item["id"] for item in before][1:] == [chosen, "person-shape"]

    undone = client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={"actor": "operator"})
    assert undone.status_code == 200, undone.text
    body = undone.json()
    assert body["status"] == "reverted" and body["reverted_by"] == "operator"
    assert body["revert_backup_version_id"].startswith("v_") and body["revert_backup_version_id"] != proposal["backup_version_id"]
    after = _labels(studio)
    assert after == [before[0], before[2]], 'only the adopted shape is gone; the original and the later person shape stay'
    assert labelme.read_bytes() == original
    # The take-back's own backup holds the labels as they were just before it.
    backup = client.get(f"/api/dataset/versions/{body['revert_backup_version_id']}").json()
    assert backup["kind"] == "auto_backup"
    assert client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={"actor": "operator"}).status_code == 409
    assert client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "accept", "candidate_ids": [chosen]}).status_code == 409


def test_an_edited_adopted_shape_cannot_be_taken_back_silently(suggestion_workspace):
    client, _, _, image, _, studio, _ = suggestion_workspace
    proposal, chosen = _adopt(client, image)

    def moved(current):
        return [{**item, "bbox": [11, 11, 21, 21]} if item.get("id") == chosen else item for item in current]

    _person_save(client, image, moved)
    edited = _labels(studio)
    refused = client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={"actor": "operator"})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["changed_candidate_ids"] == [chosen]
    assert detail["backup_version_id"] == proposal["backup_version_id"]
    assert "데이터 버전" in detail["message"]
    assert _labels(studio) == edited, 'a refused take-back writes nothing'
    assert client.get(f"/api/label-suggestions/{proposal['id']}").json()["status"] == "accepted"


def test_a_pending_or_rejected_draft_has_nothing_to_take_back(suggestion_workspace):
    client, _, _, image, _, _, _ = suggestion_workspace
    proposal = client.post("/api/label-suggestions/generate", json={"job_id": "job_12345_test", "image_path": str(image)}).json()
    assert client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={}).status_code == 409
    client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "reject", "candidate_ids": []})
    assert client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={}).status_code == 409


def test_drafts_from_the_same_batch_stay_reviewable_after_a_take_back(suggestion_workspace, monkeypatch):
    from backend.api import routes_label_suggestions

    client, project, source, first, _, studio, _ = suggestion_workspace
    second = source / "second.png"
    Image.new("RGB", (64, 48), "white").save(second)
    _refresh_receipt_fingerprint(project, source)

    def one_candidate(task, model_path, image_input, threshold=0.5, device=None):
        return SimpleNamespace(predictions=[{"bbox": [10, 10, 20, 20], "score": 0.9, "label": "defect"}],
                               confidence_score=0.9, latency_ms=1.0)

    monkeypatch.setattr(routes_label_suggestions, "infer", one_candidate)
    started = client.post("/api/label-suggestions/batches", json={"job_id": "job_12345_test", "image_paths": [str(first), str(second)]})
    assert started.status_code == 200, started.text
    batch = _wait_for_batch(client, started.json()["id"])
    first_id, second_id = (entry["proposal_id"] for entry in batch["entries"])
    first_proposal = client.get(f"/api/label-suggestions/{first_id}").json()
    accepted = client.post(f"/api/label-suggestions/{first_id}/review", json={
        "decision": "accept", "candidate_ids": [first_proposal["candidates"][0]["id"]]})
    assert accepted.status_code == 200, accepted.text
    assert client.post(f"/api/label-suggestions/{first_id}/undo", json={}).status_code == 200
    second_proposal = client.get(f"/api/label-suggestions/{second_id}").json()
    reviewed = client.post(f"/api/label-suggestions/{second_id}/review", json={
        "decision": "accept", "candidate_ids": [second_proposal["candidates"][0]["id"]]})
    assert reviewed.status_code == 200, reviewed.text
    second_labels = dataset_annotation_dir(source, Path(project["annotations_dir"]), use_scope=False) / "second.json"
    assert json.loads(second_labels.read_text())["annotations"][0]["origin"]["proposal_id"] == second_id


def test_team_editing_needs_the_reviewers_lease_to_adopt_and_to_take_back(suggestion_workspace):
    """With team editing on, adopting a draft is a label save like any other: it needs the image's edit lease.

    The reviewer acquires the lease the usual way and passes its token with the review; without it the save is
    refused and nothing is written. Taking the adoption back needs the lease the same way."""
    from backend.api import routes_team_data

    client, project, source, image, labelme, studio, _ = suggestion_workspace
    client.app.include_router(routes_annotation.router)
    client.app.include_router(routes_team_data.router)
    assert client.put("/api/team-data/settings", json={"expected_revision": 1, "actor": "owner", "changes": {"editing_enabled": True}}).status_code == 200
    proposal = client.post("/api/label-suggestions/generate", json={"job_id": "job_12345_test", "image_path": str(image)}).json()
    chosen = proposal["candidates"][0]["id"]
    original = labelme.read_bytes()

    refused = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "accept", "candidate_ids": [chosen], "actor": "Lee"})
    assert refused.status_code == 409, refused.text
    assert "편집 잠금" in refused.text and "token_hash" not in refused.text
    assert not (studio / "first.json").is_file() and labelme.read_bytes() == original
    assert client.get(f"/api/label-suggestions/{proposal['id']}").json()["status"] == "pending"

    current = client.get("/api/annotations/first", params={"file_path": str(image)}).json()["metadata"]
    lease = client.post(f"/api/team-data/images/{current['image_uuid']}/lease/acquire", json={"expected_revision": current["revision"], "actor": "Lee"})
    assert lease.status_code == 200, lease.text
    token = lease.json()["lease_token"]
    accepted = client.post(f"/api/label-suggestions/{proposal['id']}/review", json={"decision": "accept", "candidate_ids": [chosen], "actor": "Lee", "lease_token": token})
    assert accepted.status_code == 200, accepted.text
    assert "token_hash" not in accepted.text and token not in accepted.text
    saved = _labels(studio)
    assert [row["id"] for row in saved if row.get("origin")] == [chosen]
    assert saved[-1]["origin"]["reviewer"] == "Lee"

    # Another person without the lease cannot take the adoption back; the reviewer with the lease can.
    assert client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={"actor": "Kim"}).status_code == 409
    assert [row["id"] for row in _labels(studio) if row.get("origin")] == [chosen]
    reverted = client.post(f"/api/label-suggestions/{proposal['id']}/undo", json={"actor": "Lee", "lease_token": token})
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["status"] == "reverted"
    assert [row["id"] for row in _labels(studio) if row.get("origin")] == []
