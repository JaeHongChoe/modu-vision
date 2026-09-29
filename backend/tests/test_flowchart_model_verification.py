"""Saved multi-model flows keep only source-matched completed model references."""

import json

from fastapi.testclient import TestClient

from backend.api import routes_dataset, routes_flowchart
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.main import create_app


def _client_with_models(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"inspection image")
    fingerprint = fingerprint_dataset(
        source,
        studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    for job_id, task in (("job_123_detector", "detection"), ("job_456_inspector", "segmentation")):
        job_dir = tmp_path / "models" / job_id
        (job_dir / "dataset").mkdir(parents=True)
        (job_dir / "best_model.pt").write_bytes(b"trusted checkpoint placeholder")
        (job_dir / "model_meta.json").write_text(json.dumps({"task": task}), encoding="utf-8")
        (job_dir / "job_receipt.json").write_text(json.dumps({
            "status": "completed",
            "task": task,
            "source_dataset_path": str(source),
            "dataset_fingerprint": fingerprint,
            "dataset_path": str(job_dir / "dataset"),
        }), encoding="utf-8")
    app = create_app(project_dir=str(tmp_path))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token}), source


def test_saved_detector_and_inspector_models_are_verified_for_same_source(monkeypatch, tmp_path):
    client, source = _client_with_models(monkeypatch, tmp_path)
    response = client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(source),
        "models": [
            {"job_id": "job_123_detector", "task": "detection"},
            {"job_id": "job_456_inspector", "task": "segmentation"},
        ],
    })
    assert response.status_code == 200
    assert response.json()["verified_job_ids"] == ["job_123_detector", "job_456_inspector"]


def test_model_verification_rejects_changed_source_and_wrong_task(monkeypatch, tmp_path):
    client, source = _client_with_models(monkeypatch, tmp_path)
    request = {"source_dataset_path": str(source), "models": [
        {"job_id": "job_123_detector", "task": "segmentation"},
    ]}
    assert client.post("/api/flowchart/models/verify", json=request).status_code == 409
    request["models"][0]["task"] = "detection"
    (source / "image.jpg").write_bytes(b"changed inspection image")
    assert client.post("/api/flowchart/models/verify", json=request).status_code == 409


def test_detector_roi_template_exposes_the_supported_linear_chain(monkeypatch, tmp_path):
    client, _ = _client_with_models(monkeypatch, tmp_path)
    response = client.get("/api/flowchart/templates/detector-roi?inspection_task=segmentation")
    assert response.status_code == 200
    pipeline = response.json()
    assert pipeline["id"] == "detector_roi"
    assert [node["data"]["node_type"] for node in pipeline["nodes"]] == [
        "input", "detection_crop", "inspection", "decision", "output",
    ]
    assert pipeline["nodes"][2]["data"]["task"] == "segmentation"


def test_detector_roi_flow_saves_and_reopens_both_verified_model_links(monkeypatch, tmp_path):
    client, source = _client_with_models(monkeypatch, tmp_path)
    monkeypatch.setattr(routes_flowchart, "DEFAULT_PIPELINE_FILE", tmp_path / "pipeline.json")
    pipeline = client.get("/api/flowchart/templates/detector-roi?inspection_task=segmentation").json()
    pipeline["nodes"][1]["data"]["model_job_id"] = "job_123_detector"
    pipeline["nodes"][2]["data"]["model_job_id"] = "job_456_inspector"

    assert client.post("/api/flowchart/pipeline", json=pipeline).status_code == 200
    reopened = client.get("/api/flowchart/pipeline").json()
    assert reopened["nodes"][1]["data"]["model_job_id"] == "job_123_detector"
    assert reopened["nodes"][2]["data"]["model_job_id"] == "job_456_inspector"
    assert client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(source),
        "models": [
            {"job_id": reopened["nodes"][1]["data"]["model_job_id"], "task": "detection"},
            {"job_id": reopened["nodes"][2]["data"]["model_job_id"], "task": "segmentation"},
        ],
    }).status_code == 200
