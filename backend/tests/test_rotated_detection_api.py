"""Rotated candidates and their data stay in the active project."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backend.main import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _source(root: Path) -> list[dict]:
    rows = []
    for index, split in enumerate(("train", "train", "val", "test")):
        path = root / "images" / f"part_{index}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        pixels = np.full((72, 96, 3), 20 + index * 15, dtype=np.uint8)
        cx, cy, angle = 42 + index * 3, 35, -20 + index * 12
        rectangle = cv2.boxPoints(((cx, cy), (28, 12), angle))
        cv2.fillConvexPoly(pixels, np.rint(rectangle).astype(np.int32), (230, 230, 230))
        Image.fromarray(pixels).save(path)
        rows.append({
            "image": f"images/part_{index}.png", "split": split, "label": "defect",
            "box": {"cx": cx, "cy": cy, "width": 28, "height": 12, "angle_deg": angle},
        })
    return rows


def _await_terminal(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/rotated-detection/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in ("completed", "aborted", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("Rotated job did not reach a terminal state")


def test_rotated_api_trains_evaluates_predicts_and_scopes_project(tmp_path: Path):
    client = _client(tmp_path)
    first = client.post("/api/project/create", json={"name": "Rotated A", "task": "detection"})
    assert first.status_code == 200, first.text
    source = tmp_path / "source"
    rows = _source(source)

    before_source = client.post("/api/rotated-detection/manifest", json={"dataset_path": str(source), "samples": rows})
    assert before_source.status_code == 422
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    created = client.post("/api/rotated-detection/manifest", json={"dataset_path": str(source), "samples": rows})
    assert created.status_code == 200, created.text
    assert created.json()["split_counts"] == {"train": 2, "val": 1, "test": 1}
    inspected = client.get("/api/rotated-detection/manifest", params={"dataset_path": str(source)})
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["samples"][0]["source_sha256"] == hashlib.sha256(
        (source / "images/part_0.png").read_bytes()).hexdigest()

    started = client.post("/api/rotated-detection/train", json={
        "dataset_path": str(source), "epochs": 1, "batch_size": 2, "image_size": 64,
    })
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    assert started.json()["status"] in ("running", "completed")
    terminal = _await_terminal(client, job_id)
    assert terminal["status"] == "completed", terminal
    assert [row["job_id"] for row in client.get("/api/rotated-detection/models").json()["models"]] == [job_id]

    evaluated = client.post("/api/rotated-detection/evaluate", json={
        "job_id": job_id, "dataset_path": str(source), "split": "test",
    })
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["sample_count"] == 1
    predicted = client.post("/api/rotated-detection/predict", json={
        "job_id": job_id, "image_path": str(source / "images/part_3.png"),
    })
    assert predicted.status_code == 200, predicted.text
    assert len(predicted.json()["polygon"]) == 4
    assert predicted.json()["preview_data_url"].startswith("data:image/png;base64,")
    assert predicted.json()["source_sha256"] == hashlib.sha256(
        (source / "images/part_3.png").read_bytes()).hexdigest()
    outside = client.post("/api/rotated-detection/predict", json={
        "job_id": job_id, "image_path": str(tmp_path / "other.png"),
    })
    assert outside.status_code == 422

    client.post("/api/project/create", json={"name": "Rotated B", "task": "detection"})
    assert client.get("/api/rotated-detection/models").json() == {"models": []}
    denied = client.post("/api/rotated-detection/predict", json={
        "job_id": job_id, "image_path": str(source / "images/part_3.png"),
    })
    assert denied.status_code == 404


def test_rotated_api_cancel_reaches_running_job(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.post("/api/project/create", json={"name": "Rotated cancel", "task": "detection"})
    source = tmp_path / "source"
    rows = _source(source)
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    assert client.post("/api/rotated-detection/manifest", json={
        "dataset_path": str(source), "samples": rows,
    }).status_code == 200
    from backend.api import routes_rotated_detection as routes
    entered = threading.Event()

    def wait_for_cancel(*_args, cancel_event: threading.Event, **_kwargs):
        entered.set()
        assert cancel_event.wait(3)
        # Training can finish its final batch just as cancellation arrives.
        return {"status": "completed"}

    monkeypatch.setattr(routes, "train_rotated_detector", wait_for_cancel)
    started = client.post("/api/rotated-detection/train", json={"dataset_path": str(source), "epochs": 20})
    assert started.status_code == 200, started.text
    assert entered.wait(3)
    job_id = started.json()["job_id"]
    stopped = client.post(f"/api/rotated-detection/jobs/{job_id}/cancel")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] in ("stopping", "aborted")
    assert _await_terminal(client, job_id)["status"] == "aborted"
    assert client.get("/api/rotated-detection/models").json() == {"models": []}


def test_rotated_api_records_unexpected_worker_failure(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    client.post("/api/project/create", json={"name": "Rotated failure", "task": "detection"})
    source = tmp_path / "source"
    rows = _source(source)
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    assert client.post("/api/rotated-detection/manifest", json={
        "dataset_path": str(source), "samples": rows,
    }).status_code == 200
    from backend.api import routes_rotated_detection as routes

    def unexpected_failure(*_args, **_kwargs):
        raise AssertionError("unexpected worker failure")

    monkeypatch.setattr(routes, "train_rotated_detector", unexpected_failure)
    started = client.post("/api/rotated-detection/train", json={"dataset_path": str(source)})
    assert started.status_code == 200, started.text
    terminal = _await_terminal(client, started.json()["job_id"])
    assert terminal["status"] == "failed"
    assert "unexpected worker failure" in terminal["error"]
