"""Path-bound Step 1 classification splits survive a remote path change."""

from __future__ import annotations

import threading

import pytest
from PIL import Image

from backend.api import routes_dataset, routes_training
from backend.engine import dataset_loaders
from backend.engine.dataset_loaders import ClassificationDataset
from backend.remote import coordinator, ssh_transport
from backend.remote.preparation import PreparationCancelled, prepare_remote_classification
from backend.remote.profiles import ComputeProfile, get_profile_store
from backend.remote.snapshot import build_snapshot
from backend.tests.test_remote_coordinator import FakeRemote


def _images(source):
    assignments = {}
    for label in ("OK", "NG"):
        folder = source / label
        folder.mkdir(parents=True)
        for index in range(3):
            image = folder / f"{label.lower()}_{index}.png"
            Image.new("RGB", (8, 8), (index * 20, 0, 0)).save(image)
            assignments[f"{label}/{image.name}"] = ("train", "val", "test")[index]
    return assignments


def test_saved_classification_split_is_materialized_before_remote_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source"
    assignments = _images(source)
    split_dir = tmp_path / "splits"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", split_dir)
    monkeypatch.setattr(dataset_loaders, "SPLIT_MANIFEST_DIR", split_dir)
    routes_dataset._write_split_manifest(source, assignments, seed=42)

    prepared = tmp_path / "prepared"
    prepare_remote_classification(source, prepared, threading.Event())
    snapshot = build_snapshot(prepared, tmp_path / "snapshot", threading.Event())

    for partition in ("train", "val", "test"):
        expected = {path.name for path, _ in ClassificationDataset(source, split=partition).samples}
        remote = {path.name for path, _ in ClassificationDataset(snapshot.data_path, split=partition).samples}
        assert remote == expected
        assert len(remote) == 2


def test_unsaved_split_has_no_duplicate_test_partition(tmp_path):
    source = tmp_path / "source"
    _images(source)
    prepared = tmp_path / "prepared"
    prepare_remote_classification(source, prepared, threading.Event())
    assert sum(1 for _ in prepared.rglob("*.png")) == 6
    assert not (prepared / "test").exists()


def test_cancel_before_copy_publishes_no_dataset(tmp_path):
    source = tmp_path / "source"
    _images(source)
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(PreparationCancelled):
        prepare_remote_classification(source, tmp_path / "prepared", cancellation)
    assert not (tmp_path / "prepared").exists()


def test_training_api_sends_the_selected_classification_split_to_server(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    source = tmp_path / "source"
    assignments = _images(source)
    split_dir = tmp_path / "splits"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", split_dir)
    monkeypatch.setattr(dataset_loaders, "SPLIT_MANIFEST_DIR", split_dir)
    routes_dataset._write_split_manifest(source, assignments, seed=42)
    profile = ComputeProfile(
        id="server-a", name="Test server", ssh_target="test-host", ssh_port=22,
        remote_root=str(tmp_path / "server"), runtime_kind="python", runtime_value="python3",
    )
    get_profile_store().save(profile)
    fake = FakeRemote(tmp_path / "server")
    monkeypatch.setattr(coordinator, "SSHTransport", lambda: fake)
    monkeypatch.setattr(ssh_transport.SSHTransport, "probe", lambda self, profile: {"ready": True})
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, "training_job_manager", manager)

    started = routes_training.start_training(routes_training.TrainingStartRequest(
        task="classification", dataset_path=str(source), output_dir=str(tmp_path / "models"),
        compute_profile_id=profile.id,
    ))
    record = manager.get_job(started["job_id"])
    record.thread.join(timeout=10)
    assert record.status == "completed"
    assert started["compute_profile_id"] == profile.id
    assert (tmp_path / "models" / started["job_id"] / "job_receipt.json").is_file()
    for partition in ("train", "val", "test"):
        observed = {path.name for path, _ in ClassificationDataset(record.dataset_path, split=partition).samples}
        expected = {path.name for path, _ in ClassificationDataset(source, split=partition).samples}
        assert observed == expected
