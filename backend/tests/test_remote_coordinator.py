"""Remote training registers only artifacts bound to its source and server."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from backend.api.routes_training import JobRecord
from backend.remote.coordinator import reconnect_remote_training, run_remote_training
from backend.remote.profiles import ComputeProfile


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeRemote:
    def __init__(self, root: Path, *, tamper: bool = False, disconnect: bool = False,
                 cancel_upload: bool = False, cancel_after_launch: bool = False):
        self.root = root
        self.tamper = tamper
        self.disconnect = disconnect
        self.cancel_upload = cancel_upload
        self.cancel_after_launch = cancel_after_launch
        self.record = None
        self.launches = 0

    def exec(self, profile, argv, *, timeout=30):
        if argv[0] == "mkdir":
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "tar":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "cat":
            if self.disconnect:
                return subprocess.CompletedProcess(argv, 255, "", "network unavailable")
            target = Path(argv[-1])
            return subprocess.CompletedProcess(argv, 0 if target.is_file() else 1,
                                               target.read_text() if target.is_file() else "", "")
        raise AssertionError(argv)

    def upload(self, profile, local, remote_relative, *, cancel=None):
        target = self.root / remote_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, target)
        if self.cancel_upload:
            self.record.preparation_cancel.set()
        return subprocess.CompletedProcess(["upload"], 0, "", "")

    def download(self, profile, remote_relative, local):
        shutil.copy2(self.root / remote_relative, local)
        if self.tamper and remote_relative.endswith("best_model.pt"):
            Path(local).write_bytes(b"tampered checkpoint")
        return subprocess.CompletedProcess(["download"], 0, "", "")

    def launch(self, profile, argv, run_id):
        self.launches += 1
        run_dir = self.root / "runs" / run_id
        spec = json.loads((run_dir / "spec.json").read_text())
        model = b"checkpoint from fake remote trainer"
        meta = json.dumps({"task": spec["task"], "classes": ["background", "defect"]}).encode()
        artifacts = []
        for name, data in (("best_model.pt", model), ("model_meta.json", meta)):
            output = run_dir / "outputs" / name
            output.parent.mkdir(exist_ok=True)
            output.write_bytes(data)
            artifacts.append({"path": f"outputs/{name}", "size": len(data), "sha256": _digest(data)})
        (run_dir / "artifacts.json").write_text(json.dumps({
            "protocol_version": 1, "job_id": run_id, "operation": "train",
            "input_manifest_sha256": spec["input_manifest_sha256"], "artifacts": artifacts,
        }))
        (run_dir / "status.json").write_text(json.dumps({
            "protocol_version": 1, "job_id": run_id, "operation": "train",
            "status": "running" if self.cancel_after_launch else "completed",
            "device": "cuda:0", "best_metric": 0.25,
            "current_epoch": 1, "total_epochs": 1,
        }))
        if self.cancel_after_launch:
            self.record.preparation_cancel.set()
        return "fake-pid"

    def touch_cancel(self, profile, run_id):
        (self.root / "runs" / run_id / "cancel").touch()
        status = self.root / "runs" / run_id / "status.json"
        data = json.loads(status.read_text())
        data["status"] = "aborted"
        status.write_text(json.dumps(data))
        return subprocess.CompletedProcess(["touch"], 0, "", "")

    def is_running(self, profile, run_id, handle):
        return True


def _setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "training_image.png").write_bytes(b"image bytes")
    output = tmp_path / "models" / "job_1234567890_abcdef"
    profile = ComputeProfile(
        id="test-server", name="Test server", ssh_target="test-host", ssh_port=22,
        remote_root=str(tmp_path / "server"), runtime_kind="python", runtime_value="python3",
    )
    record = JobRecord(
        job_id=output.name, task="segmentation", preset="fast", dataset_path=str(source),
        output_dir=str(output), status="running", remote_profile_id=profile.id,
        source_dataset_path=str(source), dataset_fingerprint="v1:local-identity",
    )
    return source, output, profile, record


def test_remote_training_transfers_snapshot_and_registers_verified_local_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    source, output, profile, record = _setup(tmp_path)
    fake = FakeRemote(Path(profile.remote_root))
    result = run_remote_training(record, profile, transport=fake)
    assert result == {"status": "completed", "best_metric": 0.25}
    assert fake.launches == 1
    assert (output / "best_model.pt").read_bytes() == b"checkpoint from fake remote trainer"
    assert record.dataset_path == str(output / "remote_snapshot" / "data")
    assert (Path(record.dataset_path) / "training_image.png").read_bytes() == (source / "training_image.png").read_bytes()
    journal = json.loads((output / "remote_job.json").read_text())
    assert journal["state"] == "artifacts_verified"
    assert journal["profile"]["id"] == profile.id
    assert journal["source_dataset_path"] == str(source)


def test_bad_remote_checkpoint_hash_is_never_published(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    _, output, profile, record = _setup(tmp_path)
    result = run_remote_training(record, profile, transport=FakeRemote(Path(profile.remote_root), tamper=True))
    assert result["status"] == "failed"
    assert "hash mismatch" in result["error"]
    assert not (output / "best_model.pt").exists()
    assert not (output / "job_receipt.json").exists()


def test_disconnect_after_launch_preserves_unknown_run_for_reconnection(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    _, output, profile, record = _setup(tmp_path)
    result = run_remote_training(record, profile, transport=FakeRemote(Path(profile.remote_root), disconnect=True))
    assert result["status"] == "disconnected"
    assert json.loads((output / "remote_job.json").read_text())["state"] == "launched"
    assert not (output / "best_model.pt").exists()


def test_cancel_during_upload_never_launches_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    _, output, profile, record = _setup(tmp_path)
    fake = FakeRemote(Path(profile.remote_root), cancel_upload=True)
    fake.record = record
    result = run_remote_training(record, profile, transport=fake)
    assert result["status"] == "aborted"
    assert fake.launches == 0
    assert json.loads((output / "remote_job.json").read_text())["state"] == "aborted"


def test_reconnect_uses_original_remote_run_without_launching_again(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    _, output, profile, record = _setup(tmp_path)
    fake = FakeRemote(Path(profile.remote_root), disconnect=True)
    assert run_remote_training(record, profile, transport=fake)["status"] == "disconnected"
    fake.disconnect = False
    assert reconnect_remote_training(record, transport=fake)["status"] == "completed"
    assert fake.launches == 1
    assert (output / "best_model.pt").is_file()


def test_cancel_after_launch_waits_for_remote_abort_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    _, output, profile, record = _setup(tmp_path)
    fake = FakeRemote(Path(profile.remote_root), cancel_after_launch=True)
    fake.record = record
    assert run_remote_training(record, profile, transport=fake)["status"] == "aborted"
    assert fake.launches == 1
    assert (Path(profile.remote_root) / "runs" / record.job_id / "cancel").is_file()
    assert not (output / "best_model.pt").exists()


def test_worker_that_exits_before_status_is_failed_not_left_disconnected(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    from backend.remote import coordinator

    monkeypatch.setattr(coordinator, "POLL_INTERVAL_SECONDS", 0.001)
    _, output, profile, record = _setup(tmp_path)

    class DeadWorker(FakeRemote):
        def launch(self, profile, argv, run_id):
            self.launches += 1
            return "dead-pid"

        def is_running(self, profile, run_id, handle):
            return False

    result = run_remote_training(record, profile, transport=DeadWorker(Path(profile.remote_root)))
    assert result["status"] == "failed"
    assert "exited before publishing status" in result["error"]
    assert json.loads((output / "remote_job.json").read_text())["state"] == "failed"
