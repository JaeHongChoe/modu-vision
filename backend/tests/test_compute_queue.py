"""Profile-aware training reservations and restart-safe queue behavior."""

from __future__ import annotations

import json
import threading

import pytest

from backend.api import routes_training
from backend.api.routes_training import JobRecord, TrainingJobManager
from backend.remote import coordinator
from backend.remote.profiles import ComputeProfile


def _profile(tmp_path, profile_id: str, *, host: str = "gpu-host", gpu: str | None = "0") -> ComputeProfile:
    return ComputeProfile(
        id=profile_id,
        name=profile_id,
        ssh_target=f"operator@{host}",
        ssh_port=22,
        remote_root=str(tmp_path / profile_id),
        runtime_kind="python",
        runtime_value="python3",
        gpu_selector=gpu,
    )


def _start(manager, tmp_path, profile, runner, job_id):
    return manager.start_remote_job(
        job_id=job_id,
        task="segmentation",
        dataset_path=str(tmp_path / "data"),
        output_dir=str(tmp_path / job_id),
        remote_profile_id=profile.id,
        profile=profile,
        remote_runner=runner,
        launch_spec={"preparation": "none", "config_overrides": {}, "device": None},
    )


def test_remote_queue_reserves_physical_server_and_overlapping_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    manager = TrainingJobManager()
    release_gpu0 = threading.Event()
    release_gpu1 = threading.Event()
    gpu0_running = threading.Event()
    gpu1_running = threading.Event()
    queued_running = threading.Event()
    other_server_running = threading.Event()

    def hold(started, release):
        def run(_record):
            started.set()
            release.wait(timeout=5)
            return {"status": "completed"}
        return run

    first = _start(manager, tmp_path, _profile(tmp_path, "profile-a", gpu="0"),
                   hold(gpu0_running, release_gpu0), "job_gpu0")
    second = _start(manager, tmp_path, _profile(tmp_path, "profile-b", gpu="1"),
                    hold(gpu1_running, release_gpu1), "job_gpu1")
    conflict = _start(manager, tmp_path, _profile(tmp_path, "profile-c", gpu="0"),
                      lambda _record: (queued_running.set() or {"status": "completed"}), "job_queued")
    elsewhere = _start(manager, tmp_path, _profile(tmp_path, "profile-d", host="other-host", gpu="0"),
                       lambda _record: (other_server_running.set() or {"status": "completed"}), "job_other")
    try:
        assert gpu0_running.wait(timeout=2)
        assert gpu1_running.wait(timeout=2)
        assert other_server_running.wait(timeout=2)
        assert conflict.status == "queued"
        assert conflict.thread is None
        assert not queued_running.is_set()
        journal = json.loads((tmp_path / "job_queued" / "remote_job.json").read_text())
        assert journal["state"] == "queued"
        assert journal["profile"]["id"] == "profile-c"
        release_gpu0.set()
        assert queued_running.wait(timeout=2)
        conflict.thread.join(timeout=2)
        assert conflict.status == "completed"
    finally:
        release_gpu0.set()
        release_gpu1.set()
        for record in (first, second, conflict, elsewhere):
            if record.thread:
                record.thread.join(timeout=2)


def test_queued_cancel_never_calls_runner_and_persists_terminal_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    manager = TrainingJobManager()
    release = threading.Event()
    started = threading.Event()

    def hold(_record):
        started.set()
        release.wait(timeout=5)
        return {"status": "completed"}

    profile = _profile(tmp_path, "same-gpu")
    active = _start(manager, tmp_path, profile, hold, "job_active")
    called = threading.Event()
    queued = _start(manager, tmp_path, profile,
                    lambda _record: (called.set() or {"status": "completed"}), "job_cancelled")
    try:
        assert started.wait(timeout=2)
        assert queued.status == "queued"
        assert manager.abort_job(queued.job_id)
        assert queued.status == "aborted"
        assert json.loads((tmp_path / "job_cancelled" / "job_receipt.json").read_text())["status"] == "aborted"
        assert json.loads((tmp_path / "job_cancelled" / "remote_job.json").read_text())["state"] == "aborted"
        release.set()
        active.thread.join(timeout=2)
        assert not called.is_set()
    finally:
        release.set()
        active.thread.join(timeout=2)


def test_disconnect_keeps_gpu_reserved_until_same_job_reconnects(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    manager = TrainingJobManager()
    calls = 0
    queued_started = threading.Event()

    def disconnected_then_completed(_record):
        nonlocal calls
        calls += 1
        return {"status": "disconnected" if calls == 1 else "completed"}

    profile = _profile(tmp_path, "server")
    active = _start(manager, tmp_path, profile, disconnected_then_completed, "job_disconnected")
    active.thread.join(timeout=2)
    assert active.status == "disconnected"
    queued = _start(manager, tmp_path, profile,
                    lambda _record: (queued_started.set() or {"status": "completed"}), "job_waiting")
    assert queued.status == "queued"
    assert not queued_started.is_set()
    assert manager.reconnect_remote_job(active.job_id) is active
    active.thread.join(timeout=2)
    assert active.status == "completed"
    assert queued_started.wait(timeout=2)
    queued.thread.join(timeout=2)
    assert calls == 2


def test_all_gpu_selector_blocks_each_selected_gpu_on_same_server(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    manager = TrainingJobManager()
    release = threading.Event()
    started = threading.Event()

    def hold(_record):
        started.set()
        release.wait(timeout=5)
        return {"status": "completed"}

    exclusive = _start(manager, tmp_path, _profile(tmp_path, "whole-server", gpu="all"), hold, "job_all")
    selected = _start(manager, tmp_path, _profile(tmp_path, "one-gpu", gpu="1"),
                      lambda _record: {"status": "completed"}, "job_one")
    try:
        assert started.wait(timeout=2)
        assert selected.status == "queued"
    finally:
        release.set()
        exclusive.thread.join(timeout=2)
        if selected.thread:
            selected.thread.join(timeout=2)
    assert selected.status == "completed"


def test_ambiguous_gpu_selector_reserves_whole_server(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    manager = TrainingJobManager()
    release = threading.Event()

    def hold(_record):
        release.wait(timeout=5)
        return {"status": "completed"}

    ambiguous = _start(manager, tmp_path, _profile(tmp_path, "range", gpu="0:2"), hold, "job_range")
    selected = _start(manager, tmp_path, _profile(tmp_path, "one", gpu="0"),
                      lambda _record: {"status": "completed"}, "job_one")
    try:
        assert selected.status == "queued"
    finally:
        release.set()
        ambiguous.thread.join(timeout=2)
        if selected.thread:
            selected.thread.join(timeout=2)


def test_restart_reclaims_queued_job_after_reconciling_same_active_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    profile = _profile(tmp_path, "profile")
    active = JobRecord(
        job_id="job_a", task="segmentation", preset="fast", dataset_path=str(tmp_path / "data"),
        output_dir=str(tmp_path / "job_a"), status="running", remote_profile_id=profile.id,
    )
    waiting = JobRecord(
        job_id="job_b", task="segmentation", preset="fast", dataset_path=str(tmp_path / "data"),
        output_dir=str(tmp_path / "job_b"), status="queued", remote_profile_id=profile.id,
    )
    launch_spec = {"preparation": "none", "config_overrides": {}, "device": None}
    coordinator.persist_queued_remote_job(active, profile, launch_spec)
    active_journal = json.loads((tmp_path / "job_a" / "remote_job.json").read_text())
    active_journal["state"] = "launched"
    coordinator._save_journal(active_journal)
    coordinator.persist_queued_remote_job(waiting, profile, launch_spec)

    release_active = threading.Event()
    reconnected = threading.Event()
    launched_waiting = threading.Event()
    calls = []

    def reconcile(record):
        calls.append(("reconnect", record.job_id))
        reconnected.set()
        release_active.wait(timeout=5)
        return {"status": "completed"}

    def launch(record, _profile, **_kwargs):
        calls.append(("launch", record.job_id))
        launched_waiting.set()
        return {"status": "completed"}

    monkeypatch.setattr(coordinator, "reconnect_remote_training", reconcile)
    monkeypatch.setattr(coordinator, "run_remote_training", launch)
    recovered = TrainingJobManager()
    coordinator.recover_remote_jobs(recovered)
    try:
        assert reconnected.wait(timeout=2)
        assert recovered.get_job("job_b").status == "queued"
        assert not launched_waiting.is_set()
        release_active.set()
        assert launched_waiting.wait(timeout=2)
        recovered.get_job("job_a").thread.join(timeout=2)
        recovered.get_job("job_b").thread.join(timeout=2)
        assert calls == [("reconnect", "job_a"), ("launch", "job_b")]
    finally:
        release_active.set()
        for record in recovered.list_jobs():
            if record.thread:
                record.thread.join(timeout=2)


def test_training_job_controls_show_queue_order_and_cancel_waiting_job(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    manager = TrainingJobManager()
    monkeypatch.setattr(routes_training, "training_job_manager", manager)
    release = threading.Event()
    def hold(_record):
        release.wait(timeout=5)
        return {"status": "completed"}

    active = _start(manager, tmp_path, _profile(tmp_path, "profile"),
                    hold, "job_running")
    waiting = _start(manager, tmp_path, _profile(tmp_path, "other-profile"),
                     lambda _record: {"status": "completed"}, "job_waiting")
    try:
        rows = routes_training.list_training_jobs()["jobs"]
        assert [(row["job_id"], row["status"], row["queue_position"])
                for row in rows] == [("job_running", "running", None), ("job_waiting", "queued", 1)]
        stopped = routes_training.stop_training(routes_training.TrainingStopRequest(job_id="job_waiting"))
        assert stopped == {"status": "stopping", "job_id": "job_waiting"}
        assert routes_training.get_training_status(job_id="job_waiting")["status"] == "aborted"
    finally:
        release.set()
        active.thread.join(timeout=2)


@pytest.mark.parametrize("interrupted_state", ["preparing", "prepared", "transferring"])
def test_restart_marks_prelaunch_interruption_failed_without_launch(tmp_path, monkeypatch, interrupted_state):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    profile = _profile(tmp_path, "profile")
    record = JobRecord(
        job_id="job_interrupted", task="segmentation", preset="fast",
        dataset_path=str(tmp_path / "data"), output_dir=str(tmp_path / "job_interrupted"),
        status="running", remote_profile_id=profile.id,
    )
    coordinator.persist_queued_remote_job(record, profile, {"preparation": "none"})
    journal = json.loads((tmp_path / record.job_id / "remote_job.json").read_text())
    journal["state"] = interrupted_state
    coordinator._save_journal(journal)
    launched = threading.Event()
    monkeypatch.setattr(coordinator, "run_remote_training", lambda *_args, **_kwargs: launched.set())

    manager = TrainingJobManager()
    coordinator.recover_remote_jobs(manager)

    assert manager.get_job(record.job_id).status == "failed"
    assert json.loads((tmp_path / record.job_id / "job_receipt.json").read_text())["status"] == "failed"
    assert not launched.is_set()
