"""Inspection runs keep model evidence and later human decisions separate."""

import csv
import hashlib
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from PIL import Image

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.api import routes_dataset
from backend.api import routes_flowchart


def client_for(project_root):
    app = create_app(project_dir=str(project_root))
    client = TestClient(app)
    client.headers["X-Vision-Token"] = app.state.api_token
    return client


def seed_old_result(project_dir, run_id, image_path, state, result):
    """Simulate a row written by the previous client-trusting app version."""
    with sqlite3.connect(Path(project_dir) / "inspection_history.sqlite3") as conn:
        conn.execute(
            "UPDATE rows SET state = ?, result_json = ? WHERE run_id = ? AND image_path = ?",
            (state, json.dumps(result), run_id, image_path),
        )


def configured_flow(monkeypatch, tmp_path):
    """One real image, a source-matched completed checkpoint, and two saved revisions."""
    monkeypatch.chdir(tmp_path)
    client = client_for(tmp_path / "workspaces")
    created = client.post("/api/project/create", json={"name": "ceramic", "task": "segmentation"})
    assert created.status_code == 200, created.text
    project = created.json()
    source = tmp_path / "source"
    image = source / "test" / "part.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (12, 10), (20, 30, 40)).save(image)
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    job_id = "job_12345678_abcd"
    checkpoint = Path(project["models_dir"]) / job_id / "best_model.pt"
    checkpoint.parent.mkdir(parents=True)
    (checkpoint.parent / "dataset").mkdir()
    checkpoint.write_bytes(b"completed checkpoint")
    (checkpoint.parent / "model_meta.json").write_text(json.dumps({"task": "segmentation"}))
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    (checkpoint.parent / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "segmentation", "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint, "dataset_path": str(checkpoint.parent / "dataset"),
    }))
    verification = client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(source),
        "models": [{"job_id": job_id, "task": "segmentation"}],
    })
    assert verification.status_code == 200, verification.text
    flow = client.get("/api/flowchart/templates/single-segmentation", params={"job_id": job_id})
    assert flow.status_code == 200, flow.text
    first_flow = flow.json()
    first_flow["name"] = "Revision one"
    first = client.post("/api/flowchart/pipeline", params={"source_dataset_path": str(source)}, json=first_flow)
    assert first.status_code == 200, first.text
    second_flow = json.loads(json.dumps(first_flow))
    second_flow["name"] = "Revision two"
    second = client.post("/api/flowchart/pipeline", params={"source_dataset_path": str(source)}, json=second_flow)
    assert second.status_code == 200, second.text
    image_meta = client.get("/api/dataset/images", params={
        "folder_path": str(source), "task": "segmentation", "split": "test",
    }).json()["items"][0]
    payload = {
        "source_folder": str(source), "task": "segmentation", "scope": "test",
        "pipeline": client.get(f"/api/flowchart/pipelines/{second.json()['version_id']}").json(),
        "images": [image_meta],
    }
    return client, project, source, image, checkpoint, first.json()["version_id"], second.json()["version_id"], payload


def test_configured_run_binds_active_saved_flow_and_checkpoint_hash(monkeypatch, tmp_path):
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    expected_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    run = client.get(f"/api/inspections/runs/{run_id}").json()
    assert run["saved_version_id"] == second_id
    assert run["model_sha256"] == {"job_12345678_abcd": expected_hash}
    browser_graph = json.loads(json.dumps(payload["pipeline"]))
    for node in browser_graph["nodes"]:
        node["position"] = {key: int(value) for key, value in node["position"].items()}
    assert run["pipeline_hash"] == hashlib.sha256(json.dumps(
        browser_graph, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    assert client.get("/api/inspections/runs").json()["runs"][0]["saved_version_id"] == second_id
    switched = client.put(f"/api/flowchart/pipelines/{first_id}/activate", params={
        "source_dataset_path": str(source),
    })
    assert switched.status_code == 200, switched.text
    assert client.post("/api/inspections/runs", json=payload).status_code == 409
    first_flow = client.get(f"/api/flowchart/pipelines/{first_id}").json()
    first_run = client.post("/api/inspections/runs", json={**payload, "pipeline": first_flow})
    assert first_run.status_code == 200, first_run.text
    assert first_run.json()["saved_version_id"] == first_id
    assert client.get(f"/api/inspections/runs/{run_id}").json()["saved_version_id"] == second_id
    checkpoint.write_bytes(b"updated checkpoint")
    reopened = client_for(tmp_path / "workspaces")
    assert reopened.post("/api/project/open", json={"project_dir": project["project_dir"]}).status_code == 200
    old = reopened.get(f"/api/inspections/runs/{run_id}").json()
    assert old["model_sha256"] == {"job_12345678_abcd": expected_hash}
    assert old["saved_version_id"] == second_id


def test_configured_run_rejects_other_project_source_task_image_and_stale_graph(monkeypatch, tmp_path):
    client, _project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    assert client.post("/api/inspections/runs", json={**payload, "source_folder": str(tmp_path)}).status_code == 409
    assert client.post("/api/inspections/runs", json={**payload, "task": "classification"}).status_code == 409
    alien = tmp_path / "elsewhere.png"
    Image.new("RGB", (8, 8)).save(alien)
    wrong = {**payload["images"][0], "file_path": str(alien), "file_name": alien.name, "image_id": alien.stem}
    assert client.post("/api/inspections/runs", json={**payload, "images": [wrong]}).status_code == 409
    assert client.post("/api/inspections/runs", json={**payload, "images": [
        {**payload["images"][0], "split": "train"},
    ]}).status_code == 409
    changed = {**payload["pipeline"], "name": "Edited in browser"}
    assert client.post("/api/inspections/runs", json={**payload, "pipeline": changed}).status_code == 409
    first_flow = client.get(f"/api/flowchart/pipelines/{first_id}").json()
    assert client.post("/api/inspections/runs", json={**payload, "pipeline": first_flow}).status_code == 409
    image.unlink()
    assert client.post("/api/inspections/runs", json=payload).status_code == 409
    second_project = client.post("/api/project/create", json={"name": "another", "task": "segmentation"})
    assert second_project.status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert client.post("/api/inspections/runs", json=payload).status_code == 409


@pytest.mark.parametrize("alias", ["trailing_slash", "parent_segment", "symlink"])
def test_configured_run_stores_canonical_source_identity(monkeypatch, tmp_path, alias):
    client, _project, source, _image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    spellings = {
        "trailing_slash": str(source) + "/",
        "parent_segment": str(source / ".." / "source"),
        "symlink": str(tmp_path / "source_alias"),
    }
    (tmp_path / "source_alias").symlink_to(source, target_is_directory=True)
    created = client.post("/api/inspections/runs", json={**payload, "source_folder": spellings[alias]})
    assert created.status_code == 200, created.text
    saved = client.get(f"/api/inspections/runs/{created.json()['run_id']}")
    assert saved.status_code == 200, saved.text
    assert saved.json()["source_folder"] == str(source)


@pytest.mark.parametrize("legacy_alias", [False, True])
def test_inspection_source_alias_queries_keep_history_and_review_queue(monkeypatch, tmp_path, legacy_alias):
    client, project, source, image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    seed_old_result(project["project_dir"], run_id, str(image), "REVIEW", {
        "image_id": image.stem, "image_path": str(image), "final_verdict": "REVIEW",
    })
    assert client.put(f"/api/inspections/runs/{run_id}/finish", json={"status": "completed"}).status_code == 200
    if legacy_alias:
        with sqlite3.connect(Path(project["project_dir"]) / "inspection_history.sqlite3") as conn:
            conn.execute("UPDATE runs SET source_folder = ? WHERE run_id = ?", (str(source) + "/", run_id))
    link = tmp_path / "source_alias"
    link.symlink_to(source, target_is_directory=True)
    for spelling in (str(source), str(source) + "/", str(source / ".." / "source"), str(link)):
        params = {"source_folder": spelling, "task": "segmentation"}
        queued = client.get("/api/inspections/review-queue", params=params)
        assert queued.status_code == 200, queued.text
        assert queued.json()["total"] == 1
        assert queued.json()["items"][0]["run_id"] == run_id
        history = client.get("/api/inspections/runs", params=params)
        assert history.status_code == 200, history.text
        assert [run["run_id"] for run in history.json()["runs"]] == [run_id]

    assert client.get("/api/inspections/review-queue", params={
        "source_folder": str(tmp_path), "task": "segmentation",
    }).status_code == 409
    other = client.post("/api/project/create", json={"name": "other", "task": "segmentation"})
    assert other.status_code == 200, other.text
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    params = {"source_folder": str(source) + "/", "task": "segmentation"}
    assert client.get("/api/inspections/review-queue", params=params).json()["total"] == 0
    assert client.get("/api/inspections/runs", params=params).json()["runs"] == []


@pytest.mark.parametrize("alias", ["trailing_slash", "parent_segment", "symlink"])
def test_legacy_run_readback_exposes_canonical_source_without_rewriting_evidence(monkeypatch, tmp_path, alias):
    client, project, source, _image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    link = tmp_path / "source_alias"
    link.symlink_to(source, target_is_directory=True)
    spellings = {
        "trailing_slash": str(source) + "/",
        "parent_segment": str(source / ".." / "source"),
        "symlink": str(link),
    }
    database = Path(project["project_dir"]) / "inspection_history.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE runs SET source_folder = ? WHERE run_id = ?", (spellings[alias], run_id))
        original = conn.execute("SELECT source_folder, pipeline_json, pipeline_hash FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    response = client.get(f"/api/inspections/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["source_folder"] == spellings[alias]
    assert response.json()["canonical_source_folder"] == str(source)
    assert response.json()["pipeline_hash"] == original[2]
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT source_folder, pipeline_json, pipeline_hash FROM runs WHERE run_id = ?", (run_id,)).fetchone() == original


def test_legacy_run_schema_migrates_without_inventing_flow_or_model_provenance(tmp_path):
    client = client_for(tmp_path / "workspaces")
    project = client.post("/api/project/create", json={"name": "old"}).json()
    db = Path(project["project_dir"]) / "inspection_history.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, source_folder TEXT NOT NULL, task TEXT NOT NULL,
            scope TEXT NOT NULL, pipeline_id TEXT NOT NULL, pipeline_name TEXT NOT NULL,
            pipeline_hash TEXT NOT NULL, pipeline_json TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            "old-run", "/old", "classification", "all", "old-flow", "Old flow",
            "123", json.dumps({"id": "old-flow", "name": "Old flow", "nodes": [], "edges": []}),
            "completed", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z",
        ))
        conn.execute("""CREATE TABLE rows (
            run_id TEXT NOT NULL, image_path TEXT NOT NULL, image_json TEXT NOT NULL,
            state TEXT NOT NULL, result_json TEXT, error TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, image_path)
        )""")
        conn.execute("INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?, ?)", (
            "old-run", "/old/part.png", json.dumps({
                "image_id": "part", "file_name": "part.png", "file_path": "/old/part.png", "split": "all",
            }), "NG", json.dumps({"final_verdict": "NG"}), None, "2025-01-01T00:00:00Z",
        ))
    run = client.get("/api/inspections/runs/old-run")
    assert run.status_code == 200, run.text
    assert run.json()["saved_version_id"] is None
    assert run.json()["model_sha256"] == {}
    assert run.json()["rows"][0]["image_sha256"] is None
    listing = client.get("/api/inspections/runs").json()["runs"]
    assert listing[0]["saved_version_id"] is None
    assert listing[0]["model_sha256"] == {}


def test_only_server_execution_can_write_verified_model_verdict(monkeypatch, tmp_path):
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    forged = {"image_path": str(image), "image_id": image.stem, "final_verdict": "NG"}
    assert client.put(f"/api/inspections/runs/{run_id}/rows", json={
        "image_path": str(image), "state": "NG", "result": forged,
    }).status_code == 409
    assert client.get(f"/api/inspections/runs/{run_id}").json()["rows"][0]["result"] is None

    calls = []
    server_result = {
        "status": "success", "final_verdict": "NG", "is_ok": False,
        "rejection_reason": "trusted engine verdict", "roi_count": 1,
        "defective_roi_count": 1, "crops": [], "annotated_image": None,
        "execution_steps": [{"node_id": "inspection", "name": "Inspection", "status": "flagged_ng", "latency_ms": 2}],
        "total_latency_ms": 2, "image_path": str(image), "image_id": image.stem,
    }

    def trusted_run(req, request):
        calls.append((req.image_path, req.image_id, req.pipeline.model_dump()))
        return server_result

    monkeypatch.setattr(routes_flowchart, "run_flowchart", trusted_run)
    wrong = client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(source / "test" / "other.png")})
    assert wrong.status_code in (404, 409)
    assert calls == []
    executed = client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)})
    assert executed.status_code == 200, executed.text
    assert executed.json()["final_verdict"] == "NG"
    assert calls == [(str(image), image.stem, payload["pipeline"])]
    row = client.get(f"/api/inspections/runs/{run_id}").json()["rows"][0]
    assert row["state"] == "NG"
    assert row["result"]["rejection_reason"] == "trusted engine verdict"
    assert row["image_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    repeated = client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)})
    assert repeated.status_code == 200 and repeated.json() == executed.json()
    assert len(calls) == 1
    assert client.put(f"/api/inspections/runs/{run_id}/rows", json={
        "image_path": str(image), "state": "OK", "result": {**forged, "final_verdict": "OK"},
    }).status_code == 409
    assert client.put(f"/api/inspections/runs/{run_id}/finish", json={"status": "completed"}).status_code == 200
    review = client.post(f"/api/inspections/runs/{run_id}/reviews", json={
        "image_path": str(image), "final_verdict": "OK", "reviewer": "qa", "reason": "현미경 재검",
    })
    assert review.status_code == 200, review.text
    assert client.get(f"/api/inspections/runs/{run_id}").json()["rows"][0]["result"]["final_verdict"] == "NG"


def test_browser_numeric_roundtrip_keeps_saved_flow_identity_and_execution(monkeypatch, tmp_path):
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    browser_graph = json.loads(json.dumps(payload["pipeline"]))
    for node in browser_graph["nodes"]:
        node["position"] = {key: int(value) for key, value in node["position"].items()}
    assert any(isinstance(node["position"]["x"], int) for node in browser_graph["nodes"])
    browser_payload = {**payload, "pipeline": browser_graph}
    created = client.post("/api/inspections/runs", json=browser_payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    expected_hash = hashlib.sha256(json.dumps(
        browser_graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert created.json()["pipeline_hash"] == expected_hash

    def trusted_run(req, request):
        assert req.pipeline.model_dump() == payload["pipeline"]
        return {
            "status": "success", "final_verdict": "OK", "is_ok": True,
            "rejection_reason": "", "roi_count": 1, "defective_roi_count": 0,
            "crops": [], "execution_steps": [{"node_id": "inspection", "name": "Inspection", "status": "passed", "latency_ms": 1}],
            "total_latency_ms": 1, "image_path": str(image), "image_id": image.stem,
        }

    monkeypatch.setattr(routes_flowchart, "run_flowchart", trusted_run)
    executed = client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)})
    assert executed.status_code == 200, executed.text
    assert client.get(f"/api/inspections/runs/{run_id}").json()["pipeline_hash"] == expected_hash


def test_new_run_hash_matches_api_browser_numbers_and_omitted_schema_defaults(monkeypatch, tmp_path):
    client, _project, _source, _image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    browser_graph = json.loads(json.dumps(payload["pipeline"]))
    for node in browser_graph["nodes"]:
        node["position"] = {key: int(value) for key, value in node["position"].items()}
    expected_hash = hashlib.sha256(json.dumps(
        browser_graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    omitted = json.loads(json.dumps(browser_graph))
    for node in omitted["nodes"]:
        node.pop("type")
        if node["data"]["params"] == {}:
            node["data"].pop("params")
        if node["data"]["model_job_id"] is None:
            node["data"].pop("model_job_id")
    for edge in omitted["edges"]:
        for key in ("label", "isBranch", "payload_type"):
            if edge[key] is None:
                edge.pop(key)
    for graph in (payload["pipeline"], browser_graph, omitted):
        created = client.post("/api/inspections/runs", json={**payload, "pipeline": graph})
        assert created.status_code == 200, created.text
        assert created.json()["pipeline_hash"] == expected_hash
        run = client.get(f"/api/inspections/runs/{created.json()['run_id']}").json()
        assert run["pipeline"] == browser_graph
        assert all(type(node["position"]["x"]) is int for node in run["pipeline"]["nodes"])


@pytest.mark.parametrize("coordinate", [1e-7, 1e-6, 1e21])
def test_saved_flow_metadata_and_new_run_share_authoritative_numeric_hash(monkeypatch, tmp_path, coordinate):
    client, _project, source, _image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    graph = json.loads(json.dumps(payload["pipeline"]))
    graph["nodes"][0]["position"]["x"] = coordinate
    saved = client.post("/api/flowchart/pipeline", params={"source_dataset_path": str(source)}, json=graph)
    assert saved.status_code == 200, saved.text
    version_id = saved.json()["version_id"]
    listing = client.get("/api/flowchart/pipelines", params={"source_dataset_path": str(source)})
    assert listing.status_code == 200, listing.text
    version = next(row for row in listing.json()["pipelines"] if row["version_id"] == version_id)
    created = client.post("/api/inspections/runs", json={**payload, "pipeline": graph})
    assert created.status_code == 200, created.text
    assert version["pipeline_hash"] == created.json()["pipeline_hash"]
    assert len(version["pipeline_hash"]) == 64


@pytest.mark.parametrize("change", ["fractional_position", "fractional_threshold"])
def test_new_run_hash_distinguishes_fractional_graph_changes(monkeypatch, tmp_path, change):
    client, _project, source, _image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    original = client.post("/api/inspections/runs", json=payload)
    assert original.status_code == 200, original.text
    changed = json.loads(json.dumps(payload["pipeline"]))
    if change == "fractional_position":
        changed["nodes"][0]["position"]["x"] += 0.25
    else:
        inspection = next(node for node in changed["nodes"] if node["data"]["node_type"] == "inspection")
        inspection["data"]["threshold"] += 0.0001
    assert client.post("/api/inspections/runs", json={**payload, "pipeline": changed}).status_code == 409
    saved = client.post("/api/flowchart/pipeline", params={"source_dataset_path": str(source)}, json=changed)
    assert saved.status_code == 200, saved.text
    created = client.post("/api/inspections/runs", json={**payload, "pipeline": changed})
    assert created.status_code == 200, created.text
    assert created.json()["pipeline_hash"] != original.json()["pipeline_hash"]


def test_legacy_raw_graph_hash_is_preserved_and_still_checked_before_execution(monkeypatch, tmp_path):
    client, project, _source, image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    raw_json = json.dumps(payload["pipeline"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    another = client.post("/api/inspections/runs", json=payload)
    assert another.status_code == 200, another.text
    tampered_id = another.json()["run_id"]
    database = Path(project["project_dir"]) / "inspection_history.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executemany("UPDATE runs SET pipeline_json = ?, pipeline_hash = ? WHERE run_id = ?", [
            (raw_json, raw_hash, run_id), (raw_json, raw_hash, tampered_id),
        ])

    def trusted_run(req, request):
        return {
            "status": "success", "final_verdict": "OK", "is_ok": True, "rejection_reason": "",
            "roi_count": 1, "defective_roi_count": 0, "crops": [],
            "execution_steps": [{"node_id": "inspection", "name": "Inspection", "status": "passed", "latency_ms": 1}],
            "total_latency_ms": 1, "image_path": str(image), "image_id": image.stem,
        }

    monkeypatch.setattr(routes_flowchart, "run_flowchart", trusted_run)
    executed = client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)})
    assert executed.status_code == 200, executed.text
    assert client.get(f"/api/inspections/runs/{run_id}").json()["pipeline_hash"] == raw_hash
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT pipeline_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0] == raw_json
        changed = json.loads(raw_json)
        changed["nodes"][0]["position"]["x"] = int(changed["nodes"][0]["position"]["x"])
        conn.execute("UPDATE runs SET pipeline_json = ? WHERE run_id = ?", (
            json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")), tampered_id,
        ))
    assert client.post(f"/api/inspections/runs/{tampered_id}/execute", json={"image_path": str(image)}).status_code == 409


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_new_run_rejects_nonfinite_graph_numbers(monkeypatch, tmp_path, number):
    client, _project, _source, _image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    changed = json.loads(json.dumps(payload))
    changed["pipeline"]["nodes"][0]["data"]["params"]["scale"] = number
    response = client.post("/api/inspections/runs", content=json.dumps(changed), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text


def test_server_execution_rejects_modified_flow_or_checkpoint(monkeypatch, tmp_path):
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    run_id = client.post("/api/inspections/runs", json=payload).json()["run_id"]
    version_file = Path(project["project_dir"]) / "flowcharts" / "versions" / f"{second_id}.json"
    saved = json.loads(version_file.read_text())
    saved["pipeline"]["name"] = "tampered version file"
    version_file.write_text(json.dumps(saved))
    assert client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)}).status_code == 409
    saved["pipeline"] = payload["pipeline"]
    version_file.write_text(json.dumps(saved))
    checkpoint.write_bytes(b"different checkpoint")
    assert client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)}).status_code == 409


def test_delayed_finish_and_read_stay_bound_to_original_project_after_switch(monkeypatch, tmp_path):
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    run_id = client.post("/api/inspections/runs", json=payload).json()["run_id"]
    allow_finish = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: (allow_finish.wait(5), client.put(
            f"/api/inspections/runs/{run_id}/finish", json={"status": "stopped"},
        ))[1])
        second_project = client.post("/api/project/create", json={"name": "other", "task": "segmentation"})
        assert second_project.status_code == 200, second_project.text
        allow_finish.set()
        finished = future.result(timeout=10)
    assert finished.status_code == 200, finished.text
    assert client.get("/api/inspections/runs").json()["runs"] == []
    original = client.get(f"/api/inspections/runs/{run_id}")
    assert original.status_code == 200
    assert original.json()["status"] == "stopped"
    assert original.json()["rows"][0]["state"] == "skipped"
    reopened = client_for(tmp_path / "workspaces")
    assert reopened.get(f"/api/inspections/runs/{run_id}").json()["status"] == "stopped"


def test_execution_uses_original_checkpoint_and_project_when_switched_mid_run(monkeypatch, tmp_path):
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    run_id = client.post("/api/inspections/runs", json=payload).json()["run_id"]
    entered, release = Event(), Event()

    def trusted_run(req, request):
        assert routes_flowchart._ENGINE._resolve_checkpoint("job_12345678_abcd", "segmentation") == checkpoint
        entered.set()
        assert release.wait(5)
        assert routes_flowchart._ENGINE._resolve_checkpoint("job_12345678_abcd", "segmentation") == checkpoint
        return {
            "status": "success", "final_verdict": "OK", "is_ok": True,
            "rejection_reason": "", "roi_count": 1, "defective_roi_count": 0,
            "crops": [], "execution_steps": [{"node_id": "inspection", "name": "Inspection", "status": "passed", "latency_ms": 1}],
            "total_latency_ms": 1, "image_path": str(image), "image_id": image.stem,
        }

    monkeypatch.setattr(routes_flowchart, "run_flowchart", trusted_run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/api/inspections/runs/{run_id}/execute", json={"image_path": str(image)})
        assert entered.wait(5)
        other = client.post("/api/project/create", json={"name": "other", "task": "segmentation"})
        assert other.status_code == 200, other.text
        other_checkpoint = Path(other.json()["models_dir"]) / "job_12345678_abcd" / "best_model.pt"
        other_checkpoint.parent.mkdir(parents=True)
        other_checkpoint.write_bytes(b"other project's same job id")
        release.set()
        executed = future.result(timeout=10)
    assert executed.status_code == 200, executed.text
    assert executed.json()["final_verdict"] == "OK"
    assert client.get("/api/inspections/runs").json()["runs"] == []
    assert client.get(f"/api/inspections/runs/{run_id}").json()["rows"][0]["state"] == "OK"


def test_inspection_run_survives_restart_and_keeps_original_verdict(tmp_path):
    root = tmp_path / "workspaces"
    client = client_for(root)
    project = client.post("/api/project/create", json={"name": "ceramic", "task": "segmentation"})
    assert project.status_code == 200
    project_dir = project.json()["project_dir"]
    payload = {
        "source_folder": "/data/ceramic/ng",
        "task": "segmentation",
        "scope": "test",
        "pipeline": {"id": "flow-one", "name": "QC", "nodes": [], "edges": []},
        "images": [{
            "image_id": "img-1", "file_path": "/data/ceramic/ng/part-1.png",
            "file_name": "part-1.png", "split": "test", "thumbnail_url": "/thumbnail/1",
        }],
    }
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    result = {
        "status": "success", "final_verdict": "NG", "image_id": "img-1",
        "image_path": "/data/ceramic/ng/part-1.png", "execution_steps": [
            {"node_id": "model", "name": "defect", "status": "flagged_ng", "latency_ms": 12.0}
        ], "crops": [], "roi_count": 0, "defective_roi_count": 1,
        "total_latency_ms": 12.0, "rejection_reason": "defect",
    }
    row = {"image_path": result["image_path"], "state": "NG", "result": result}
    saved = client.put(f"/api/inspections/runs/{run_id}/rows", json=row)
    assert saved.status_code == 409, saved.text
    seed_old_result(project_dir, run_id, result["image_path"], "NG", result)
    changed = client.put(f"/api/inspections/runs/{run_id}/rows", json={**row, "state": "OK", "result": {**result, "final_verdict": "OK"}})
    assert changed.status_code == 409

    finished = client.put(f"/api/inspections/runs/{run_id}/finish", json={"status": "completed"})
    assert finished.status_code == 200, finished.text
    review = client.post(f"/api/inspections/runs/{run_id}/reviews", json={
        "image_path": result["image_path"], "final_verdict": "OK",
        "reason": "현미경 재검 결과 정상", "reviewer": "operator-a",
    })
    assert review.status_code == 200, review.text
    run = client.get(f"/api/inspections/runs/{run_id}").json()
    assert run["rows"][0]["result"]["final_verdict"] == "NG"
    assert run["rows"][0]["review"]["final_verdict"] == "OK"
    assert len(run["rows"][0]["reviews"]) == 1

    reopened = client_for(root)
    assert reopened.post("/api/project/open", json={"project_dir": project_dir}).status_code == 200
    readback = reopened.get(f"/api/inspections/runs/{run_id}")
    assert readback.status_code == 200
    assert readback.json()["rows"][0]["result"]["final_verdict"] == "NG"
    assert readback.json()["rows"][0]["review"]["final_verdict"] == "OK"
    listing = reopened.get("/api/inspections/runs", params={"source_folder": payload["source_folder"]})
    assert listing.status_code == 200
    assert listing.json()["runs"][0]["run_id"] == run_id


def test_review_rejects_uninspected_row(tmp_path):
    client = client_for(tmp_path)
    assert client.post("/api/project/create", json={"name": "ceramic"}).status_code == 200
    created = client.post("/api/inspections/runs", json={
        "source_folder": "/data/ceramic/ng", "task": "classification", "scope": "test",
        "pipeline": {"id": "f", "name": "test", "nodes": [], "edges": []},
        "images": [{"image_id": "1", "file_path": "/data/part.png", "file_name": "part.png", "split": "test"}],
    })
    run_id = created.json()["run_id"]
    response = client.post(f"/api/inspections/runs/{run_id}/reviews", json={
        "image_path": "/data/part.png", "final_verdict": "OK",
        "reason": "재검", "reviewer": "operator-a",
    })
    assert response.status_code == 409


def test_abandoned_run_is_marked_stopped_after_backend_restart(tmp_path, monkeypatch):
    from backend.api import routes_inspections

    client = client_for(tmp_path)
    assert client.post("/api/project/create", json={"name": "ceramic"}).status_code == 200
    created = client.post("/api/inspections/runs", json={
        "source_folder": "/data/ceramic/ng", "task": "classification", "scope": "test",
        "pipeline": {"id": "f", "name": "test", "nodes": [], "edges": []},
        "images": [{"image_id": "1", "file_path": "/data/part.png", "file_name": "part.png", "split": "test"}],
    })
    run_id = created.json()["run_id"]
    monkeypatch.setattr(routes_inspections, "PROCESS_INSTANCE", "new-backend-process")
    recovered = client.get(f"/api/inspections/runs/{run_id}")
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "stopped"
    assert recovered.json()["rows"][0]["state"] == "skipped"


def test_csv_export_does_not_turn_image_or_review_text_into_spreadsheet_formulas(tmp_path):
    client = client_for(tmp_path)
    project = client.post("/api/project/create", json={"name": "ceramic"})
    assert project.status_code == 200
    image_path = '/data/=HYPERLINK("https://example.com","open").png'
    image_id = '=HYPERLINK("https://example.com","open")'
    created = client.post("/api/inspections/runs", json={
        "source_folder": "/data", "task": "classification", "scope": "test",
        "pipeline": {"id": "flow", "name": "test", "nodes": [], "edges": []},
        "images": [{"image_id": image_id, "file_path": image_path, "file_name": "formula.png", "split": "test"}],
    })
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    result = {"image_id": image_id, "image_path": image_path, "final_verdict": "REVIEW"}
    assert client.put(f"/api/inspections/runs/{run_id}/rows", json={
        "image_path": image_path, "state": "REVIEW", "result": result,
    }).status_code == 409
    seed_old_result(project.json()["project_dir"], run_id, image_path, "REVIEW", result)
    assert client.put(f"/api/inspections/runs/{run_id}/finish", json={"status": "completed"}).status_code == 200
    assert client.post(f"/api/inspections/runs/{run_id}/reviews", json={
        "image_path": image_path, "final_verdict": "REVIEW", "reviewer": "=1+1", "reason": "  =HYPERLINK(test)",
    }).status_code == 200
    exported = client.get(f"/api/inspections/runs/{run_id}/export", params={"format": "csv"})
    assert exported.status_code == 200
    row = list(csv.DictReader(io.StringIO(exported.json()["content"])))[0]
    for column in ("image_id", "reviewer", "review_reason"):
        assert row[column].startswith("'"), (column, row[column])
    assert row["image_path"] == image_path
    literal = client.get(f"/api/inspections/runs/{run_id}/export", params={"format": "json"}).json()
    assert json.loads(literal["content"])["rows"][0]["image"]["image_id"] == image_id


def test_review_queue_tracks_unresolved_reviews_and_diagnostic_errors(monkeypatch, tmp_path):
    client, project, source, image, _checkpoint, _first, _second, payload = configured_flow(monkeypatch, tmp_path)
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    seed_old_result(project["project_dir"], run_id, str(image), "REVIEW", {
        "image_id": image.stem, "image_path": str(image), "final_verdict": "REVIEW",
    })
    assert client.put(f"/api/inspections/runs/{run_id}/finish", json={"status": "completed"}).status_code == 200
    params = {"source_folder": str(source), "task": "segmentation"}
    queued = client.get("/api/inspections/review-queue", params=params)
    assert queued.status_code == 200, queued.text
    assert queued.json()["total"] == 1
    assert queued.json()["items"][0]["status"] == "unreviewed"
    assert queued.json()["items"][0]["run_id"] == run_id
    assert queued.json()["items"][0]["image"]["file_path"] == str(image)
    assert client.post(f"/api/inspections/runs/{run_id}/reviews", json={
        "image_path": str(image), "final_verdict": "REVIEW", "reviewer": "operator", "reason": "Need another look",
    }).status_code == 200
    assert client.get("/api/inspections/review-queue", params=params).json()["items"][0]["status"] == "still_review"
    assert client.post(f"/api/inspections/runs/{run_id}/reviews", json={
        "image_path": str(image), "final_verdict": "NG", "reviewer": "operator", "reason": "Confirmed defect",
    }).status_code == 200
    assert client.get("/api/inspections/review-queue", params=params).json()["total"] == 0

    manually_open = client.post("/api/inspections/runs", json=payload)
    manual_id = manually_open.json()["run_id"]
    seed_old_result(project["project_dir"], manual_id, str(image), "NG", {
        "image_id": image.stem, "image_path": str(image), "final_verdict": "NG",
    })
    assert client.put(f"/api/inspections/runs/{manual_id}/finish", json={"status": "completed"}).status_code == 200
    assert client.post(f"/api/inspections/runs/{manual_id}/reviews", json={
        "image_path": str(image), "final_verdict": "REVIEW", "reviewer": "operator", "reason": "Needs second observer",
    }).status_code == 200
    queued = client.get("/api/inspections/review-queue", params=params).json()
    assert queued["total"] == 1
    assert queued["items"][0]["status"] == "still_review"

    failed = client.post("/api/inspections/runs", json=payload)
    failed_id = failed.json()["run_id"]
    assert client.put(f"/api/inspections/runs/{failed_id}/rows", json={
        "image_path": str(image), "state": "error", "error": "Inference unavailable",
    }).status_code == 200
    assert client.put(f"/api/inspections/runs/{failed_id}/finish", json={"status": "completed"}).status_code == 200
    queued = client.get("/api/inspections/review-queue", params={**params, "limit": 1}).json()
    assert queued["total"] == 2
    assert queued["review_required"] == 1
    assert queued["diagnostic_errors"] == 1
    assert queued["items"][0]["status"] == "error"
    assert queued["items"][0]["can_review"] is False

    second = client.post("/api/project/create", json={"name": "other", "task": "segmentation"})
    assert second.status_code == 200
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert client.get("/api/inspections/review-queue", params=params).json()["total"] == 0


def test_evidence_image_reads_only_the_exact_hashed_run_source(monkeypatch, tmp_path):
    import base64
    client, project, source, image, checkpoint, first_id, second_id, payload = configured_flow(monkeypatch, tmp_path)
    run_id = client.post('/api/inspections/runs', json=payload).json()['run_id']
    params = {'image_path': str(image)}
    endpoint = f'/api/inspections/runs/{run_id}/evidence-image'
    # A pending/legacy row without a captured hash cannot claim historical identity.
    assert client.get(endpoint, params=params).status_code == 409
    expected = hashlib.sha256(image.read_bytes()).hexdigest()
    with sqlite3.connect(Path(project['project_dir']) / 'inspection_history.sqlite3') as conn:
        conn.execute('UPDATE rows SET image_sha256=? WHERE run_id=?', (expected, run_id))
    result = client.get(endpoint, params=params)
    assert result.status_code == 200, result.text
    actual = result.json()
    assert actual['run_id'] == run_id and actual['saved_version_id'] == second_id
    assert actual['image_path'] == str(image) and actual['image_sha256'] == expected
    assert actual['original_size'] == [12, 10] and actual['read_only'] is True
    decoded = Image.open(io.BytesIO(base64.b64decode(actual['image'].split(',', 1)[1])))
    assert decoded.getpixel((0, 0)) == (20, 30, 40)
    outside = tmp_path / 'outside.png'; Image.new('RGB', (12,10)).save(outside)
    assert client.get(endpoint, params={'image_path': str(outside)}).status_code == 404
    Image.new('RGB', (12,10), (90,80,70)).save(image)
    assert client.get(endpoint, params=params).status_code == 409
    image.unlink();image.symlink_to(outside)
    assert client.get(endpoint, params=params).status_code == 409
    # Viewing never writes a result, decision, saved source hash or image.
    row = client.get(f'/api/inspections/runs/{run_id}').json()['rows'][0]
    assert row['image_sha256'] == expected and row['state'] == 'pending' and row['result'] is None
