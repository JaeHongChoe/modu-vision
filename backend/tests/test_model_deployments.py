"""Approval records must bind to verified model evidence, not a flow draft."""

import hashlib
import json
from pathlib import Path

import torch
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_dataset
from backend.api.routes_model_deployments import verified_approval_revision
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.main import create_app


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _client(workspace: Path) -> TestClient:
    app = create_app(project_dir=str(workspace))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _fixture(tmp_path: Path, ok_count: int = 8, ng_count: int = 8):
    client = _client(tmp_path / "workspaces")
    source = tmp_path / "source"
    for label, count in (("OK", ok_count), ("NG", ng_count)):
        folder = source / "test" / label
        folder.mkdir(parents=True)
        for index in range(count):
            Image.new("RGB", (8, 8), "white" if label == "OK" else "black").save(folder / f"{label.lower()}_{index:02d}.png")
    project = client.post("/api/project/create", json={"name": "Approval", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    project["source_dataset_dir"] = str(source)
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    models = {}
    for job_id in ("job_base", "job_candidate", "job_third"):
        root = Path(project["models_dir"]) / job_id
        root.mkdir(parents=True)
        checkpoint = root / "best_model.pt"
        checkpoint.write_bytes(job_id.encode())
        (root / "model_meta.json").write_text(json.dumps({"task": "classification"}))
        (root / "job_receipt.json").write_text(json.dumps({
            "status": "completed", "task": "classification", "source_dataset_path": str(source),
            "dataset_fingerprint": fingerprint,
        }))
        models[job_id] = checkpoint
    return client, project, source, fingerprint, models


def _report(project: dict, source: Path, fingerprint: str, models: dict, *,
            incumbent: str = "job_base", candidate: str = "job_candidate",
            comparison_id: str = "comparison_" + "a" * 32) -> dict:
    images = []
    for path in sorted((source / "test").glob("*/*.png")):
        truth = path.parent.name
        images.append({
            "file_path": str(path), "image_id": path.stem, "file_name": path.name,
            "image_sha256": _sha(path), "ground_truth_verdict": truth,
            "incumbent": {"verdict": truth}, "candidate": {"verdict": truth},
        })
    known_ok = sum(row["ground_truth_verdict"] == "OK" for row in images)
    known_ng = sum(row["ground_truth_verdict"] == "NG" for row in images)
    report = {
        "comparison_id": comparison_id, "project_id": project["id"],
        "source_dataset_path": str(source), "task": "classification",
        "incumbent_job_id": incumbent, "candidate_job_id": candidate,
        "model_sha256": {"incumbent": _sha(models[incumbent]), "candidate": _sha(models[candidate])},
        "dataset_fingerprint": fingerprint,
        "selected_image_count": len(images), "total_test_images": len(images),
        "status": "completed", "images": images,
        "summary": {
            "selected_images": len(images), "comparable_images": len(images),
            "error_images": 0, "known_ok_images": known_ok, "known_ng_images": known_ng,
            "unknown_truth_images": 0, "new_missed_ng": 0, "new_overkill_ok": 0,
        },
    }
    output = Path(project["reports_dir"]) / "model_comparisons"
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{comparison_id}.json").write_text(json.dumps(report))
    return report


def _params(source: Path) -> dict:
    return {"source_dataset_path": str(source), "task": "classification"}


def _approve(client: TestClient, source: Path, comparison_id: str):
    return client.post("/api/model-deployments/approve", json={
        **_params(source), "comparison_id": comparison_id,
        "reviewer": "qa-lead", "reason": "Held-out OK and NG samples checked",
        "holdout_reviewed": True,
    })


def test_missing_ok_cohort_stays_needs_review_and_cannot_activate(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path, ok_count=0, ng_count=8)
    report = _report(project, source, fingerprint, models)
    assessment = client.get(f"/api/model-deployments/assess/{report['comparison_id']}", params=_params(source))
    assert assessment.status_code == 200, assessment.text
    assert assessment.json()["status"] == "needs_review"
    assert any("OK" in reason for reason in assessment.json()["reasons"])
    assert _approve(client, source, report["comparison_id"]).status_code == 409
    assert client.get("/api/model-deployments/active", params=_params(source)).json()["active"] is None


def test_approved_candidate_and_rollback_are_immutable_and_survive_restart(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    first = _report(project, source, fingerprint, models)
    flow_pointer = Path(project["project_dir"]) / "flowcharts" / "active.json"
    flow_pointer.parent.mkdir()
    flow_pointer.write_text('{"version_id":"saved-flow-is-separate"}')
    approval = _approve(client, source, first["comparison_id"])
    assert approval.status_code == 200, approval.text
    first_revision = approval.json()["revision"]
    assert first_revision["job_id"] == "job_candidate"
    assert first_revision["checkpoint_sha256"] == _sha(models["job_candidate"])
    assert first_revision["action"] == "approve"
    mixed_project = {**project, "task": "segmentation"}
    assert verified_approval_revision(mixed_project, first_revision["revision_id"], expected_task="classification")
    assert verified_approval_revision(mixed_project, first_revision["revision_id"], expected_task="segmentation") is None
    report_file = Path(project["reports_dir"]) / "model_comparisons" / f"{first['comparison_id']}.json"
    original_report_bytes = report_file.read_bytes()
    report_file.write_bytes(original_report_bytes + b" ")
    assert verified_approval_revision(mixed_project, first_revision["revision_id"], expected_task="classification") is None
    report_file.write_bytes(original_report_bytes)
    assert flow_pointer.read_text() == '{"version_id":"saved-flow-is-separate"}'
    second = _report(project, source, fingerprint, models, incumbent="job_candidate", candidate="job_third",
                     comparison_id="comparison_" + "b" * 32)
    promoted = _approve(client, source, second["comparison_id"])
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["revision"]["parent_revision_id"] == first_revision["revision_id"]
    rollback = client.post("/api/model-deployments/rollback", json={
        **_params(source), "target_revision_id": first_revision["revision_id"],
        "reviewer": "qa-lead", "reason": "Restore approved model after review",
    })
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["revision"]["action"] == "rollback"
    assert rollback.json()["revision"]["restored_from_revision_id"] == first_revision["revision_id"]
    reopened = _client(tmp_path / "workspaces")
    assert reopened.post("/api/project/open", json={"project_dir": project["project_dir"]}).status_code == 200
    active = reopened.get("/api/model-deployments/active", params=_params(source)).json()["active"]
    assert active["revision_id"] == rollback.json()["revision"]["revision_id"]
    assert active["job_id"] == "job_candidate"
    history = reopened.get("/api/model-deployments/history", params=_params(source)).json()["revisions"]
    assert [row["action"] for row in history] == ["rollback", "approve", "approve"]
    assert flow_pointer.read_text() == '{"version_id":"saved-flow-is-separate"}'


def test_stale_checkpoint_or_image_blocks_approval(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    report = _report(project, source, fingerprint, models)
    models["job_candidate"].write_bytes(b"new checkpoint")
    assessment = client.get(f"/api/model-deployments/assess/{report['comparison_id']}", params=_params(source))
    assert assessment.json()["status"] == "needs_review"
    assert _approve(client, source, report["comparison_id"]).status_code == 409
    models["job_candidate"].write_bytes(b"job_candidate")
    image = source / "test" / "NG" / "ng_00.png"
    Image.new("RGB", (8, 8), "red").save(image)
    assessment = client.get(f"/api/model-deployments/assess/{report['comparison_id']}", params=_params(source))
    assert assessment.json()["status"] == "needs_review"
    assert _approve(client, source, report["comparison_id"]).status_code == 409


def test_approval_requires_explicit_reviewer_attestation(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    report = _report(project, source, fingerprint, models)
    rejected = client.post("/api/model-deployments/approve", json={
        **_params(source), "comparison_id": report["comparison_id"],
        "reviewer": "qa-lead", "reason": "Review", "holdout_reviewed": False,
    })
    assert rejected.status_code == 422
    blanks = client.post("/api/model-deployments/approve", json={
        **_params(source), "comparison_id": report["comparison_id"],
        "reviewer": "   ", "reason": "          ", "holdout_reviewed": True,
    })
    assert blanks.status_code == 422


def test_warm_started_candidate_requires_parent_holdout_and_intact_lineage(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    lineage = {
        "parent_job_id": "job_base",
        "parent_checkpoint_sha256": _sha(models["job_base"]),
        "parent_dataset_fingerprint": fingerprint,
    }
    candidate_dir = models["job_candidate"].parent
    meta_path = candidate_dir / "model_meta.json"
    receipt_path = candidate_dir / "job_receipt.json"
    meta = json.loads(meta_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    meta["warm_start"] = lineage
    receipt["warm_start"] = lineage
    meta_path.write_text(json.dumps(meta))
    receipt_path.write_text(json.dumps(receipt))
    torch.save({"task": "classification", "warm_start": lineage,
                "model_state_dict": {"marker": torch.tensor(1)}}, models["job_candidate"])

    wrong_baseline = _report(project, source, fingerprint, models, incumbent="job_third")
    assessed = client.get(f"/api/model-deployments/assess/{wrong_baseline['comparison_id']}", params=_params(source))
    assert assessed.json()["status"] == "needs_review"
    assert _approve(client, source, wrong_baseline["comparison_id"]).status_code == 409

    matched = _report(project, source, fingerprint, models, comparison_id="comparison_" + "c" * 32)
    assessed = client.get(f"/api/model-deployments/assess/{matched['comparison_id']}", params=_params(source))
    assert assessed.json()["status"] == "ready"
    models["job_base"].write_bytes(b"changed parent")
    assessed = client.get(f"/api/model-deployments/assess/{matched['comparison_id']}", params=_params(source))
    assert assessed.json()["status"] == "needs_review"
    assert _approve(client, source, matched["comparison_id"]).status_code == 409


def test_warm_start_approval_checks_checkpoint_lineage_not_only_metadata(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    lineage = {
        "parent_job_id": "job_base",
        "parent_checkpoint_sha256": _sha(models["job_base"]),
        "parent_dataset_fingerprint": fingerprint,
    }
    candidate_dir = models["job_candidate"].parent
    (candidate_dir / "model_meta.json").write_text(json.dumps({
        "task": "classification", "warm_start": lineage,
    }))
    receipt = json.loads((candidate_dir / "job_receipt.json").read_text())
    receipt["warm_start"] = lineage
    (candidate_dir / "job_receipt.json").write_text(json.dumps(receipt))
    forged = {**lineage, "parent_checkpoint_sha256": "0" * 64}
    torch.save({"task": "classification", "warm_start": forged,
                "model_state_dict": {"marker": torch.tensor(1)}}, models["job_candidate"])
    report = _report(project, source, fingerprint, models)
    assessed = client.get(f"/api/model-deployments/assess/{report['comparison_id']}", params=_params(source))
    assert assessed.json()["status"] == "needs_review"
    assert _approve(client, source, report["comparison_id"]).status_code == 409


def test_patch_candidate_uses_same_holdout_approval_and_history(tmp_path: Path):
    client, project, source, fingerprint, models = _fixture(tmp_path)
    for checkpoint in models.values():
        metadata = checkpoint.parent / "model_meta.json"
        receipt = checkpoint.parent / "job_receipt.json"
        metadata.write_text(json.dumps({"task": "patch_classification"}))
        current = json.loads(receipt.read_text())
        current["task"] = "patch_classification"
        receipt.write_text(json.dumps(current))
    report = _report(project, source, fingerprint, models)
    report["task"] = "patch_classification"
    report_file = Path(project["reports_dir"]) / "model_comparisons" / f"{report['comparison_id']}.json"
    report_file.write_text(json.dumps(report))
    params = {"source_dataset_path": str(source), "task": "patch_classification"}
    assessed = client.get(f"/api/model-deployments/assess/{report['comparison_id']}", params=params)
    assert assessed.status_code == 200, assessed.text
    assert assessed.json()["status"] == "ready"
    approved = client.post("/api/model-deployments/approve", json={
        **params, "comparison_id": report["comparison_id"], "reviewer": "qa-lead",
        "reason": "Held-out OK and NG patch-source images checked", "holdout_reviewed": True,
    })
    assert approved.status_code == 200, approved.text
    active = client.get("/api/model-deployments/active", params=params)
    assert active.status_code == 200, active.text
    assert active.json()["active"]["job_id"] == "job_candidate"
    assert active.json()["active"]["task"] == "patch_classification"


def test_flow_export_binds_active_approval_revision_and_checkpoint_hash(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client, project, source, fingerprint, models = _fixture(tmp_path)
    for checkpoint in models.values():
        torch.save({"task": "classification", "model_state_dict": {}}, checkpoint)
        (checkpoint.parent / "dataset").mkdir()
    report = _report(project, source, fingerprint, models)
    pipeline = get_single_segmentation_flowchart(job_id="job_candidate")
    next(node for node in pipeline.nodes if node.data.node_type == "inspection").data.task = "classification"
    saved = client.post("/api/flowchart/pipeline", params={
        "recipe_task": "classification", "source_dataset_path": str(source),
    }, json=pipeline.model_dump())
    assert saved.status_code == 200, saved.text

    request = {
        "source_dataset_path": str(source), "recipe_task": "classification",
        "package_name": "controlled_flow",
        "approval_revision_ids": {"job_candidate": "a" * 32},
    }
    unapproved = client.post("/api/export/flow", json=request)
    assert unapproved.status_code == 409, unapproved.text
    assert not (Path(project["project_dir"]) / "exports/flows/controlled_flow").exists()

    approval = _approve(client, source, report["comparison_id"])
    assert approval.status_code == 200, approval.text
    revision_id = approval.json()["revision"]["revision_id"]
    request["approval_revision_ids"] = {"job_candidate": revision_id}
    checkpoint = models["job_candidate"]
    approved_bytes = checkpoint.read_bytes()
    torch.save({"task": "classification", "model_state_dict": {"changed": True}}, checkpoint)
    mismatch = client.post("/api/export/flow", json=request)
    assert mismatch.status_code == 409
    assert not (Path(project["project_dir"]) / "exports/flows/controlled_flow").exists()
    checkpoint.write_bytes(approved_bytes)

    exported = client.post("/api/export/flow", json=request)
    assert exported.status_code == 200, exported.text
    package = Path(exported.json()["package_path"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["release"]["approval_revisions"] == [{
        "revision_id": revision_id, "job_id": "job_candidate", "task": "classification",
        "checkpoint_sha256": _sha(checkpoint),
    }]
    assert exported.json()["release_policy"] is None
    assert exported.json()["release_policy_withheld"] == "cohort_parity_required"
    assert exported.json()["parity"]["status"] == "not_run"
