"""Remote jobs keep the local training API's lifecycle and provenance."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi import HTTPException

from backend.api.routes_training import TrainingJobManager


def _start(manager: TrainingJobManager, tmp_path, runner, *, job_id="job_1234567890_abcdef"):
    return manager.start_remote_job(
        job_id=job_id,
        task="segmentation",
        dataset_path=str(tmp_path / "prepared"),
        output_dir=str(tmp_path / "models" / job_id),
        preset="fast",
        remote_profile_id="server-42",
        remote_runner=runner,
        source_dataset_path=str(tmp_path / "source"),
        dataset_fingerprint="v1:local-source-version",
    )


def test_remote_start_returns_before_worker_and_completes_only_after_artifacts(tmp_path):
    manager = TrainingJobManager()
    running = threading.Event()
    release = threading.Event()

    def runner(record):
        assert record.remote_profile_id == "server-42"
        assert record.dataset_path == str(tmp_path / "prepared")
        running.set()
        release.wait(timeout=3)
        (tmp_path / "models" / record.job_id).mkdir(parents=True, exist_ok=True)
        (tmp_path / "models" / record.job_id / "best_model.pt").write_bytes(b"verified-model")
        return {"status": "completed", "best_metric": 0.25}

    record = _start(manager, tmp_path, runner)
    try:
        assert record.job_id == "job_1234567890_abcdef"
        assert running.wait(timeout=2)
        assert manager.is_training
        assert record.status == "running"
        assert not (tmp_path / "models" / record.job_id / "job_receipt.json").exists()
    finally:
        release.set()
        record.thread.join(timeout=3)

    assert record.status == "completed"
    receipt = json.loads((tmp_path / "models" / record.job_id / "job_receipt.json").read_text())
    assert receipt["source_dataset_path"] == str(tmp_path / "source")
    assert receipt["dataset_fingerprint"] == "v1:local-source-version"
    assert receipt["compute_profile_id"] == "server-42"
    assert receipt["dataset_path"] == str(tmp_path / "prepared")


def test_remote_cancel_is_job_owned_and_app_shutdown_does_not_cancel_it(tmp_path):
    manager = TrainingJobManager()
    running = threading.Event()

    def runner(record):
        running.set()
        assert record.preparation_cancel.wait(timeout=3)
        return {"status": "aborted"}

    record = _start(manager, tmp_path, runner)
    assert running.wait(timeout=2)
    manager.abort_all()
    assert not record.preparation_cancel.is_set(), "closing the app must not abort a detached remote run"
    with pytest.raises(HTTPException) as blocked:
        _start(manager, tmp_path, lambda _: {"status": "completed"}, job_id="job_1234567890_abcdee")
    assert blocked.value.status_code == 409
    assert manager.abort_job(record.job_id)
    assert record.status == "stopping"
    record.thread.join(timeout=3)
    assert record.status == "aborted"
    assert not manager.is_training


def test_network_loss_is_not_a_terminal_receipt_or_permission_to_start_over(tmp_path):
    manager = TrainingJobManager()
    record = _start(manager, tmp_path, lambda _: {"status": "disconnected"})
    record.thread.join(timeout=3)
    assert record.status == "disconnected"
    assert manager.is_training
    assert not (tmp_path / "models" / record.job_id / "job_receipt.json").exists()
    with pytest.raises(HTTPException) as blocked:
        _start(manager, tmp_path, lambda _: {"status": "completed"}, job_id="job_1234567890_abcdee")
    assert blocked.value.status_code == 409
