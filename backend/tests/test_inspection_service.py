"""Standalone package service: durable work and failure evidence."""

from __future__ import annotations

import os
import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backend.main  # noqa: F401 - test suite's Starlette/httpx compatibility patch
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_detection_flowchart


@pytest.fixture
def package(tmp_path: Path) -> Path:
    model = tmp_path / "job_detector" / "best_model.pt"
    model.parent.mkdir()
    model.write_bytes(b"fixture checkpoint")
    result = build_flow_package(
        pipeline=get_single_detection_flowchart(job_id="job_detector"),
        checkpoints={"job_detector": model},
        output_base_dir=tmp_path / "exports",
        package_name="service_fixture",
    )
    return Path(result["package_path"])


def _wait(client: TestClient, job_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        row = response.json()
        if row["state"] == expected:
            return row
        time.sleep(0.02)
    pytest.fail(f"Job {job_id} did not reach {expected}")


def test_service_requires_token_and_records_packaged_verdict(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32), (25, 50, 75)).save(image)
    observed = []

    def inspect(_package: Path, image_path: Path, image_id=None,*,device=None,**_options):
        observed.append((image_path,device))
        return {"final_verdict": "NG", "roi_count": 1, "crops": [], "execution_steps": []}

    monkeypatch.setattr(inspection_service, "run_flow_package", inspect)
    app = inspection_service.create_service_app(package, tmp_path / "state", token="secret")
    with TestClient(app) as client:
        assert client.post("/v1/jobs/file", json={"image_path": str(image)}).status_code == 401
        client.headers.update({"X-Vision-Token": "secret"})
        queued = client.post("/v1/jobs/file", json={"image_path": str(image)})
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]
        row = _wait(client, job_id, "completed")
        assert row["verdict"] == "NG"
        assert row["result"]["roi_count"] == 1
        assert observed == [(image.resolve(),'cpu')]
        assert [item["state"] for item in client.get(f"/v1/jobs/{job_id}/events").json()["events"]] == [
            "queued", "running", "completed",
        ]


def test_restart_reclaims_queued_job_and_preserves_history(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32), (25, 50, 75)).save(image)
    state = tmp_path / "state"
    first = inspection_service.create_service_app(package, state, token="secret", auto_worker=False)
    with TestClient(first) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        job_id = client.post("/v1/jobs/file", json={"image_path": str(image)}).json()["job_id"]
        assert client.get(f"/v1/jobs/{job_id}").json()["state"] == "queued"

    monkeypatch.setattr(inspection_service, "run_flow_package", lambda *_args, **_kw: {
        "final_verdict": "OK", "roi_count": 0, "crops": [], "execution_steps": [],
    })
    second = inspection_service.create_service_app(package, state, token="secret")
    with TestClient(second) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        assert _wait(client, job_id, "completed")["verdict"] == "OK"
        assert len(client.get("/v1/jobs").json()["jobs"]) == 1


def test_changed_or_missing_image_is_review_never_ok(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32), (25, 50, 75)).save(image)
    state = tmp_path / "state"
    app = inspection_service.create_service_app(package, state, token="secret", auto_worker=False)
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        job_id = client.post("/v1/jobs/file", json={"image_path": str(image)}).json()["job_id"]
    image.write_bytes(b"changed after queue")
    monkeypatch.setattr(inspection_service, "run_flow_package", lambda *_args, **_kw: pytest.fail("must not infer"))
    resumed = inspection_service.create_service_app(package, state, token="secret")
    with TestClient(resumed) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        row = _wait(client, job_id, "error")
        assert row["verdict"] == "REVIEW"
        assert "changed" in row["error"].lower()


def test_package_tamper_rejected_before_service_starts(package: Path, tmp_path: Path):
    from backend.engine.inspection_service import create_service_app

    (package / "models" / "job_detector" / "best_model.pt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        create_service_app(package, tmp_path / "state", token="secret")
    assert not (tmp_path / "state").exists()


def test_required_release_policy_blocks_unapproved_package_before_state(package: Path, tmp_path: Path):
    from backend.engine.inspection_service import create_service_app

    manifest_sha = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
    policy = tmp_path / "release-policy.json"
    policy.write_text(json.dumps({
        "schema_version": 1, "manifest_sha256": manifest_sha,
        "approval_revisions": [{
            "revision_id": "a" * 32, "job_id": "job_detector", "task": "detection",
            "checkpoint_sha256": hashlib.sha256(
                (package / "models/job_detector/best_model.pt").read_bytes(),
            ).hexdigest(),
        }],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="approved release"):
        create_service_app(
            package, tmp_path / "unapproved_state", token="secret",
            require_approved_release=True, release_policy=policy,
        )
    assert not (tmp_path / "unapproved_state").exists()


def test_approved_release_policy_accepts_exact_package_and_rejects_stale_manifest(tmp_path: Path):
    from backend.engine.inspection_service import create_service_app

    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.tests.runtime_release_fixture import real_classification_checkpoints,cohort_receipt,bind_policy
    checkpoint = tmp_path / "job_detector" / "best_model.pt"
    checkpoint.parent.mkdir()
    real_classification_checkpoints({'job_detector':checkpoint})
    graph=get_single_segmentation_flowchart(job_id='job_detector')
    for node in graph.nodes:
        if node.data.node_type=='inspection':node.data.task='classification'
    result = build_flow_package(
        pipeline=graph,checkpoints={"job_detector": checkpoint},
        output_base_dir=tmp_path / "exports", package_name="approved_service",
        approved_revisions={"job_detector": {
            "revision_id": "b" * 32, "job_id": "job_detector", "task": "classification",
            "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        }},
    )
    package = Path(result["package_path"])
    policy = tmp_path / "release-policy.json"
    images=[]
    for number,color in enumerate(('white','black')):
        image=tmp_path/f'parity_{number}.png';Image.new('RGB',(32,32),color).save(image);images.append(image)
    cohort_receipt(package,graph,{'job_detector':checkpoint},images)
    result['release_policy']=bind_policy(result['release_policy'],package)
    policy.write_text(json.dumps(result["release_policy"]), encoding="utf-8")
    app = create_service_app(
        package, tmp_path / "approved_state", token="secret",
        require_approved_release=True, release_policy=policy, auto_worker=False,
    )
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ready"

    stale = {**result["release_policy"], "manifest_sha256": "0" * 64}
    policy.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest SHA-256"):
        create_service_app(
            package, tmp_path / "stale_state", token="secret",
            require_approved_release=True, release_policy=policy,
        )
    assert not (tmp_path / "stale_state").exists()


def test_http_image_upload_is_queued_and_invalid_image_is_rejected(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    observed: list[Path] = []
    monkeypatch.setattr(inspection_service, "run_flow_package", lambda _pkg, image_path, image_id=None, **_options: (
        observed.append(image_path) or {"final_verdict": "REVIEW", "roi_count": 0, "crops": [], "execution_steps": []}
    ))
    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32)).save(image)
    app = inspection_service.create_service_app(package, tmp_path / "state", token="secret")
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        assert client.post("/v1/jobs/upload", content=b"not image").status_code == 422
        queued = client.post("/v1/jobs/upload", content=image.read_bytes())
        assert queued.status_code == 202, queued.text
        assert _wait(client, queued.json()["job_id"], "completed")["verdict"] == "REVIEW"
        assert len(observed) == 1 and observed[0].is_file()


def test_folder_adapter_ingests_each_image_version_once_across_restart(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    image = inbox / "part.png"
    Image.new("RGB", (32, 32), (20, 20, 20)).save(image)
    os.utime(image, (time.time() - 5, time.time() - 5))
    monkeypatch.setattr(inspection_service, "run_flow_package", lambda *_args, **_kw: {
        "final_verdict": "NG", "roi_count": 1, "crops": [], "execution_steps": [],
    })
    state = tmp_path / "state"
    app = inspection_service.create_service_app(package, state, token="secret", inbox_dir=inbox)
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(client.get("/v1/jobs").json()["jobs"]) < 1:
            time.sleep(0.05)
        jobs = client.get("/v1/jobs").json()["jobs"]
        assert len(jobs) == 1
        assert _wait(client, jobs[0]["job_id"], "completed")["verdict"] == "NG"
    restarted = inspection_service.create_service_app(package, state, token="secret", inbox_dir=inbox)
    with TestClient(restarted) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        time.sleep(0.25)
        assert len(client.get("/v1/jobs").json()["jobs"]) == 1
        Image.new("RGB", (32, 32), (70, 70, 70)).save(image)
        os.utime(image, (time.time() - 5, time.time() - 5))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(client.get("/v1/jobs").json()["jobs"]) < 2:
            time.sleep(0.05)
        assert len(client.get("/v1/jobs").json()["jobs"]) == 2


def test_runtime_disconnect_is_review_and_explicit_retry_keeps_events(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32)).save(image)
    def disconnected(*_args, **_kwargs):
        raise ConnectionError("GPU server disconnected")
    monkeypatch.setattr(inspection_service, "run_flow_package", disconnected)
    app = inspection_service.create_service_app(package, tmp_path / "state", token="secret")
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        job_id = client.post("/v1/jobs/file", json={"image_path": str(image)}).json()["job_id"]
        failed = _wait(client, job_id, "error")
        assert failed["verdict"] == "REVIEW"
        assert "disconnected" in failed["error"]
        monkeypatch.setattr(inspection_service, "run_flow_package", lambda *_args, **_kw: {
            "final_verdict": "NG", "roi_count": 1, "crops": [], "execution_steps": [],
        })
        assert client.post(f"/v1/jobs/{job_id}/retry").status_code == 202
        assert _wait(client, job_id, "completed")["verdict"] == "NG"
        events = client.get(f"/v1/jobs/{job_id}/events").json()["events"]
        assert [event["state"] for event in events] == [
            "queued", "running", "error", "queued", "running", "completed",
        ]


def test_device_result_delivery_failure_keeps_operational_verdict_review(package: Path, tmp_path: Path, monkeypatch):
    from backend.engine import inspection_service

    image = tmp_path / "source.png"
    Image.new("RGB", (32, 32)).save(image)
    monkeypatch.setattr(inspection_service, "run_flow_package", lambda *_args, **_kw: {
        "final_verdict": "OK", "roi_count": 0, "crops": [], "execution_steps": [],
    })
    def offline(*_args, **_kw):
        raise ConnectionError("MES endpoint unreachable")
    monkeypatch.setattr(inspection_service.httpx, "post", offline)
    app = inspection_service.create_service_app(
        package, tmp_path / "state", token="secret", result_webhook_url="http://device.test/result",
    )
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        job_id = client.post("/v1/jobs/file", json={"image_path": str(image)}).json()["job_id"]
        row = _wait(client, job_id, "delivery_error")
        assert row["model_verdict"] == "OK"
        assert row["verdict"] == "REVIEW"
        assert "unreachable" in row["error"]

        class Accepted:
            status_code = 200

        monkeypatch.setattr(inspection_service.httpx, "post", lambda *_args, **_kw: Accepted())
        assert client.post(f"/v1/jobs/{job_id}/retry-delivery").status_code == 202
        assert _wait(client, job_id, "completed")["verdict"] == "OK"


def test_camera_adapter_records_frame_then_reports_disconnect(package: Path, tmp_path: Path, monkeypatch):
    import numpy as np
    from backend.engine import inspection_service

    class Camera:
        def __init__(self):
            self.reads = 0

        def isOpened(self):
            return True

        def read(self):
            self.reads += 1
            if self.reads == 1:
                return True, np.full((32, 32, 3), 128, dtype=np.uint8)
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(inspection_service.cv2, "VideoCapture", lambda _source: Camera())
    monkeypatch.setattr(inspection_service, "run_flow_package", lambda *_args, **_kw: {
        "final_verdict": "NG", "roi_count": 1, "crops": [], "execution_steps": [],
    })
    app = inspection_service.create_service_app(
        package, tmp_path / "state", token="secret", camera_source="camera-1",
    )
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            jobs = client.get("/v1/jobs").json()["jobs"]
            status = client.get("/v1/adapters").json()["camera"]
            if jobs and status == "disconnected":
                break
            time.sleep(0.05)
        assert len(jobs) == 1
        assert _wait(client, jobs[0]["job_id"], "completed")["verdict"] == "NG"
        assert status == "disconnected"


def test_device_trigger_is_idempotent_and_missing_capture_is_review(package: Path, tmp_path: Path):
    from backend.engine.inspection_service import create_service_app

    app = create_service_app(package, tmp_path / "state", token="secret")
    payload = {
        "device_id": "plc_01", "event_id": "lot42_frame1",
        "image_path": str(tmp_path / "missing-frame.png"),
    }
    with TestClient(app) as client:
        client.headers.update({"X-Vision-Token": "secret"})
        first = client.post("/v1/device-events", json=payload)
        repeated = client.post("/v1/device-events", json=payload)
        assert first.status_code == 202
        assert repeated.status_code == 202
        assert first.json()["job_id"] == repeated.json()["job_id"]
        row = client.get(f"/v1/jobs/{first.json()['job_id']}").json()
        assert row["source"] == "device:plc_01"
        assert row["state"] == "error"
        assert row["verdict"] == "REVIEW"
        assert "missing" in row["error"].lower()
