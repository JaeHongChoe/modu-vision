"""Cancellation boundaries while preparing flat LabelMe training data."""

import json
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_dataset, routes_training
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.labelme_preparation import prepare_labelme_segmentation


def _write_labelme_pairs(folder, count=3):
    folder.mkdir()
    for index in range(count):
        image = folder / f"ng_{index:04d}.jpg"
        Image.new("RGB", (64, 64), color=(100, 100, 100)).save(image)
        image.with_suffix(".json").write_text(json.dumps({
            "imagePath": image.name,
            "imageWidth": 64,
            "imageHeight": 64,
            "shapes": [{"label": "Bow", "shape_type": "polygon",
                        "points": [[20, 20], [30, 20], [25, 30]]}],
        }), encoding="utf-8")


def test_start_returns_job_id_while_labelme_preparation_is_blocked(monkeypatch, tmp_path):
    source = tmp_path / "source"
    _write_labelme_pairs(source)
    preparing = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    response = {}
    training_started = threading.Event()

    def slow_preparation(*args, **kwargs):
        preparing.set()
        release.wait(timeout=5)
        return {"train": 2, "val": 1, "test": 0, "unlabelled": 0}

    class Trainer:
        def __init__(self, **kwargs):
            pass

        def abort(self):
            pass

        def train(self, job_id):
            training_started.set()
            return {"status": "completed"}

    monkeypatch.setattr(routes_training, "prepare_labelme_segmentation", slow_preparation)
    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", Trainer)
    # This test isolates preparation cancellation from process-wide GC/cache cleanup.
    monkeypatch.setattr(routes_training, "clear_device_cache", lambda: None)
    manager = routes_training.TrainingJobManager(local_execution='embedded')
    monkeypatch.setattr(routes_training, "training_job_manager", manager)

    def start():
        try:
            response.update(routes_training.start_training(routes_training.TrainingStartRequest(
                task="segmentation", dataset_path=str(source), output_dir=str(tmp_path / "models"),
            )))
        finally:
            returned.set()

    caller = threading.Thread(target=start, daemon=True)
    caller.start()
    try:
        assert preparing.wait(timeout=2)
        assert returned.wait(timeout=1), "Start waited for the entire LabelMe preparation"
        job_id = response["job_id"]
        assert manager.get_job(job_id).status == "running"
        assert routes_training.stop_training(routes_training.TrainingStopRequest(job_id=job_id))["status"] == "stopping"
        assert routes_training.get_training_status(job_id=job_id)["is_training"]
        with pytest.raises(Exception) as blocked:
            manager.start_job("overlap", "segmentation", str(source), str(tmp_path / "overlap"))
        assert getattr(blocked.value, "status_code", None) == 409
    finally:
        release.set()
        caller.join(timeout=2)
        assert not caller.is_alive(), "Training start did not return"
        for record in list(manager._jobs.values()):
            if record.thread:
                record.thread.join(timeout=2)
                assert not record.thread.is_alive(), "Cancelled worker did not finish"

    record = manager.get_job(job_id)
    assert record.status == "aborted"
    assert not manager.is_training
    assert not training_started.is_set(), "Trainer ran after preparation was cancelled"


def test_labelme_preparation_can_abort_before_processing_sources(tmp_path):
    source = tmp_path / "source"
    _write_labelme_pairs(source)
    output = tmp_path / "prepared"

    with pytest.raises(RuntimeError, match="cancel"):
        prepare_labelme_segmentation(
            source, output, image_size=64, cancellation_requested=lambda: True,
        )

    assert not list(output.rglob("*.png"))


def test_start_prepares_studio_only_segmentation_labels(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", tmp_path / "annotations")
    monkeypatch.setattr(routes_training, "clear_device_cache", lambda: None)
    source = tmp_path / "studio_only"
    source.mkdir()
    studio_dir = dataset_annotation_dir(source, tmp_path / "annotations")
    studio_dir.mkdir(parents=True)
    for index in range(3):
        image = source / f"ng_{index:04d}.jpg"
        Image.new("RGB", (64, 64), color=(100, 100, 100)).save(image)
        (studio_dir / f"{image.stem}.json").write_text(json.dumps({
            "image_id": image.stem,
            "annotations": [{"type": "polygon", "label": "Bow",
                             "polygon": [[20, 20], [30, 20], [25, 30]]}],
        }), encoding="utf-8")

    class Trainer:
        def __init__(self, **kwargs):
            self.dataset_path = Path(kwargs["dataset_path"])

        def abort(self):
            pass

        def train(self, job_id):
            assert (self.dataset_path / "source_manifest.json").is_file()
            return {"status": "completed"}

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", Trainer)
    manager = routes_training.TrainingJobManager(local_execution='embedded')
    monkeypatch.setattr(routes_training, "training_job_manager", manager)
    response = routes_training.start_training(routes_training.TrainingStartRequest(
        task="segmentation", dataset_path=str(source), output_dir=str(tmp_path / "models"),
        config_overrides={"image_size": 64},
    ))
    record = manager.get_job(response["job_id"])
    record.thread.join(timeout=3)

    assert record.status == "completed"
    manifest = json.loads((Path(record.output_dir) / "dataset" / "source_manifest.json").read_text())
    assert len(manifest) == 3
    assert all(row["annotation_source"] == "studio" for row in manifest)


def test_training_start_rejects_a_file_as_dataset_folder(tmp_path):
    source_file = tmp_path / "not_a_folder.jpg"
    source_file.write_bytes(b"not a dataset")
    with pytest.raises(HTTPException) as error:
        routes_training.start_training(routes_training.TrainingStartRequest(
            task="segmentation", dataset_path=str(source_file), output_dir=str(tmp_path / "models"),
        ))
    assert error.value.status_code == 400
    assert not (tmp_path / "models").exists()


def test_training_start_rejects_partial_existing_split_manifest(monkeypatch, tmp_path):
    source = tmp_path / "source"
    _write_labelme_pairs(source)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    first_image = sorted(source.glob("*.jpg"))[0]
    routes_dataset._write_split_manifest(source, {first_image.name: "train"}, seed=42)

    with pytest.raises(HTTPException) as error:
        routes_training.start_training(routes_training.TrainingStartRequest(
            task="segmentation", dataset_path=str(source), output_dir=str(tmp_path / "models"),
        ))
    assert error.value.status_code == 422
    assert "split" in str(error.value.detail).lower()
    assert not (tmp_path / "models").exists()


def test_preparation_rejects_late_label_missing_from_required_split(tmp_path):
    source = tmp_path / "source"
    _write_labelme_pairs(source)
    first_image = sorted(source.glob("*.jpg"))[0]
    with pytest.raises(ValueError, match="split"):
        prepare_labelme_segmentation(
            source, tmp_path / "prepared", image_size=64,
            assignments={str(first_image): "train"}, require_complete_assignments=True,
        )


@pytest.mark.parametrize("terminal", ["completed", "aborted", "failed"])
def test_terminal_training_job_writes_recoverable_receipt(monkeypatch, tmp_path, terminal):
    class Trainer:
        def __init__(self, **kwargs):
            pass

        def abort(self):
            pass

        def train(self, job_id):
            if terminal == "failed":
                raise RuntimeError("training failed")
            return {"status": terminal}

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", Trainer)
    # Terminal receipt persistence is independent of global GC/accelerator cleanup latency.
    monkeypatch.setattr(routes_training, "clear_device_cache", lambda: None)
    manager = routes_training.TrainingJobManager(local_execution='embedded')
    output = tmp_path / terminal
    record = manager.start_job(
        f"job_{terminal}", "segmentation", str(tmp_path), str(output),
    )
    record.thread.join(timeout=2)
    assert not record.thread.is_alive(), "Terminal worker did not finish"

    assert record.status == terminal
    assert json.loads((output / "job_receipt.json").read_text()) == {
        "job_id": f"job_{terminal}",
        "status": terminal,
        "task": "segmentation",
        "dataset_path": str(tmp_path),
        "output_dir": str(output),
        "current_epoch": 0, "total_epochs": 0,
        "current_step": 0, "total_steps": 0,
        "current_train_loss": None, "current_val_loss": None,
        "best_metric": None, "metrics": {}, "loss_history": [],
        "error": record.error,
    }


@pytest.mark.parametrize("terminal", ["completed", "aborted", "failed"])
def test_embedded_job_retains_reservation_until_cache_cleanup_finishes(monkeypatch, tmp_path, terminal):
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()

    class Trainer:
        def __init__(self, **kwargs):
            pass

        def train(self, job_id):
            if terminal == "failed":
                raise RuntimeError("training failed")
            return {"status": terminal}

    def blocked_cleanup():
        cleanup_entered.set()
        assert release_cleanup.wait(timeout=5), "Test did not release cache cleanup"

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", Trainer)
    monkeypatch.setattr(routes_training, "clear_device_cache", blocked_cleanup)
    manager = routes_training.TrainingJobManager(local_execution='embedded')
    output = tmp_path / terminal
    record = manager.start_job(f"job_cleanup_{terminal}", "segmentation", str(tmp_path), str(output))
    try:
        assert cleanup_entered.wait(timeout=2), "Worker did not reach cache cleanup"
        assert record.status == "running"
        assert manager.is_training
        assert not (output / "job_receipt.json").exists()
        assert any(row['job_id'] == record.job_id for row in manager._leases.list())
        with pytest.raises(HTTPException) as overlap:
            manager.start_job("overlap_cleanup", "segmentation", str(tmp_path), str(tmp_path / "overlap"))
        assert overlap.value.status_code == 409
    finally:
        release_cleanup.set()
        record.thread.join(timeout=2)
        assert not record.thread.is_alive(), "Worker did not finish after cache cleanup"

    assert record.status == terminal
    assert json.loads((output / "job_receipt.json").read_text(encoding='utf-8'))['status'] == terminal
    assert not manager.is_training
    assert all(row['job_id'] != record.job_id for row in manager._leases.list())
