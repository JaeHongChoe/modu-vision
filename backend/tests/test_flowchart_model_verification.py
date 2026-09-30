"""Saved multi-model flows keep only source-matched completed model references."""

import json
from pathlib import Path

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


def test_model_catalog_only_lists_completed_models_for_current_source(monkeypatch, tmp_path):
    client, source = _client_with_models(monkeypatch, tmp_path)
    incomplete = tmp_path / "models" / "job_999_unfinished"
    incomplete.mkdir(parents=True)
    (incomplete / "best_model.pt").write_bytes(b"incomplete")
    (incomplete / "job_receipt.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")

    response = client.get("/api/flowchart/models/catalog", params={"source_dataset_path": str(source)})

    assert response.status_code == 200
    models = response.json()["models"]
    assert {(model["job_id"], model["task"]) for model in models} == {
        ("job_123_detector", "detection"),
        ("job_456_inspector", "segmentation"),
    }
    assert all(model["label"] and model["created_at"] for model in models)
    (source / "image.jpg").write_bytes(b"changed inspection image")
    assert client.get("/api/flowchart/models/catalog", params={"source_dataset_path": str(source)}).json()["models"] == []


def test_catalog_reports_checkpoint_thresholds_label_revision_and_parent(monkeypatch, tmp_path):
    client, source = _client_with_models(monkeypatch, tmp_path)
    meta = tmp_path / 'models' / 'job_456_inspector' / 'model_meta.json'
    meta.write_text(json.dumps({'task': 'segmentation', 'optimal_threshold': .65, 'probability_threshold': .7,
        'min_defect_area_px': 9, 'training_labelset_id': 'revision-a',
        'warm_start': {'parent_job_id': 'job_parent'}}))
    models = client.get('/api/flowchart/models/catalog', params={'source_dataset_path': str(source)}).json()['models']
    model = next(row for row in models if row['job_id'] == 'job_456_inspector')
    assert model['threshold_settings'] == {'optimal_threshold': .65, 'probability_threshold': .7, 'min_defect_area_px': 9}
    assert model['training_labelset_id'] == 'revision-a'
    assert model['parent_job_id'] == 'job_parent'


def test_project_model_wins_when_legacy_global_job_has_same_id(monkeypatch, tmp_path):
    client, source = _client_with_models(monkeypatch, tmp_path)
    project = client.get("/api/project/current").json()
    job_id = "job_123_detector"
    local = tmp_path / "models" / job_id
    target = Path(project["models_dir"]) / job_id
    target.mkdir(parents=True)
    for name in ("best_model.pt", "model_meta.json", "job_receipt.json"):
        (target / name).write_bytes((local / name).read_bytes())
    stale = json.loads((local / "job_receipt.json").read_text(encoding="utf-8"))
    stale["source_dataset_path"] = str(tmp_path / "another_source")
    (local / "job_receipt.json").write_text(json.dumps(stale), encoding="utf-8")

    verified = client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(source),
        "models": [{"job_id": job_id, "task": "detection"}],
    })
    assert verified.status_code == 200, verified.text
    catalog = client.get("/api/flowchart/models/catalog", params={"source_dataset_path": str(source)})
    assert catalog.status_code == 200, catalog.text
    assert job_id in {item["job_id"] for item in catalog.json()["models"]}
