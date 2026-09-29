"""Regressions observed with the NG_labelme inspection data."""

import threading
import base64
import io
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image
import json
import numpy as np

from backend.api import routes_dataset, routes_evaluation, routes_flowchart, routes_training
from backend.api import routes_annotation
from backend.engine.flowchart_engine import FlowchartRunRequest, get_default_flowchart
from backend.engine import exporter
from backend.engine.industrial_adapters import HierarchicalClassificationAdapter


def test_training_manager_starts_without_locking_itself(monkeypatch, tmp_path):
    class FastTrainer:
        def __init__(self, **kwargs):
            pass

        def train(self, job_id):
            return {"status": "completed", "model_path": str(tmp_path / "best_model.pt")}

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", FastTrainer)
    manager = routes_training.TrainingJobManager()
    result = []

    caller = threading.Thread(
        target=lambda: result.append(manager.start_job(
            job_id="qa_job", task="segmentation", dataset_path=str(tmp_path),
            output_dir=str(tmp_path),
        )),
        daemon=True,
    )
    caller.start()
    caller.join(timeout=1)
    assert not caller.is_alive(), "start_job deadlocked before creating a job"
    assert result[0].job_id == "qa_job"
    result[0].thread.join(timeout=2)
    assert result[0].status == "completed"


def test_training_cancel_returns_immediately_and_waits_for_worker_exit(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    class SlowTrainer:
        def __init__(self, **kwargs):
            self.abort_requested = False

        def train(self, job_id):
            started.set()
            release.wait(timeout=5)
            return {"status": "completed"}

        def abort(self):
            self.abort_requested = True

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", SlowTrainer)
    manager = routes_training.TrainingJobManager()
    record = manager.start_job("cancel_qa", "segmentation", str(tmp_path), str(tmp_path))
    assert started.wait(timeout=2)
    try:
        import time

        started_at = time.monotonic()
        assert manager.abort_job(record.job_id)
        assert time.monotonic() - started_at < 1.0
        assert record.status == "stopping"
        assert record.thread.is_alive()
        assert manager.is_training
        with pytest.raises(HTTPException) as error:
            manager.start_job("overlap_qa", "segmentation", str(tmp_path), str(tmp_path))
        assert error.value.status_code == 409
    finally:
        release.set()
        record.thread.join(timeout=2)
    assert not record.thread.is_alive()
    assert record.status == "aborted"
    assert manager.get_active_job() is None


def test_cancel_keeps_slot_busy_until_device_cleanup_finishes(monkeypatch, tmp_path):
    started = threading.Event()
    finish_train = threading.Event()
    cleanup_started = threading.Event()
    finish_cleanup = threading.Event()

    class SlowTrainer:
        def __init__(self, **kwargs):
            pass

        def train(self, job_id):
            started.set()
            finish_train.wait(timeout=5)
            return {"status": "completed"}

        def abort(self):
            pass

    def slow_cleanup():
        cleanup_started.set()
        finish_cleanup.wait(timeout=5)

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", SlowTrainer)
    monkeypatch.setattr(routes_training, "clear_device_cache", slow_cleanup)
    manager = routes_training.TrainingJobManager()
    record = manager.start_job("cleanup_qa", "segmentation", str(tmp_path), str(tmp_path))
    assert started.wait(timeout=2)
    try:
        assert manager.abort_job(record.job_id)
        finish_train.set()
        assert cleanup_started.wait(timeout=2)
        assert record.status == "stopping"
        assert manager.is_training
        with pytest.raises(HTTPException) as error:
            manager.start_job("too_early", "segmentation", str(tmp_path), str(tmp_path))
        assert error.value.status_code == 409
    finally:
        finish_train.set()
        finish_cleanup.set()
        record.thread.join(timeout=2)
    assert record.status == "aborted"
    assert not manager.is_training


def test_cancel_after_last_batch_skips_validation_and_checkpoint(tmp_path):
    from backend.engine.trainer import TrainingCallback, UnifiedAutoMLTrainer

    data = tmp_path / "dataset"
    for split in ("train", "val"):
        image_dir = data / "images" / split
        mask_dir = data / "masks" / split
        image_dir.mkdir(parents=True)
        mask_dir.mkdir(parents=True)
        Image.new("RGB", (64, 64), color=(100, 100, 100)).save(image_dir / "sample.png")
        Image.new("L", (64, 64), color=1).save(mask_dir / "sample.png")

    class AbortAfterStep(TrainingCallback):
        def __init__(self):
            self.trainer = None
            self.completed = False
            self.aborted = False

        def on_step_end(self, *args):
            self.trainer.abort()

        def on_training_completed(self, *args):
            self.completed = True

        def on_training_aborted(self, *args):
            self.aborted = True

    callback = AbortAfterStep()
    trainer = UnifiedAutoMLTrainer(
        task="segmentation", dataset_path=data, output_dir=tmp_path / "model",
        device="cpu", callback=callback, config_overrides={"epochs": 1, "image_size": 64},
    )
    callback.trainer = trainer
    result = trainer.train(job_id="cancel_final_batch")
    assert result["status"] == "aborted"
    assert callback.aborted and not callback.completed
    assert not (tmp_path / "model" / "best_model.pt").exists()


def test_training_stop_routes_report_stopping_then_aborted(monkeypatch, tmp_path):
    release = threading.Event()

    class SlowTrainer:
        def __init__(self, **kwargs):
            pass

        def train(self, job_id):
            release.wait(timeout=5)
            return {"status": "completed"}

        def abort(self):
            pass

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", SlowTrainer)
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, "training_job_manager", manager)
    started = routes_training.start_training(routes_training.TrainingStartRequest(
        task="segmentation", dataset_path=str(tmp_path),
        output_dir=str(tmp_path / "models"),
    ))
    job_id = started["job_id"]
    try:
        stopped = routes_training.stop_training(routes_training.TrainingStopRequest(job_id=job_id))
        assert stopped["status"] == "stopping"
        pending = routes_training.get_training_status(job_id=job_id)
        assert pending["status"] == "stopping" and pending["is_training"]
    finally:
        release.set()
        manager.get_job(job_id).thread.join(timeout=2)
    finished = routes_training.get_training_status(job_id=job_id)
    assert finished["status"] == "aborted" and not finished["is_training"]


def test_three_way_split_counts_and_filters_match_real_files(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "split_manifests", raising=False)
    for index in range(20):
        Image.new("RGB", (8, 8), color=(index, 0, 0)).save(tmp_path / f"sample_{index:02d}.jpg")

    response = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(tmp_path), train_ratio=0.7, val_ratio=0.2, test_ratio=0.1, seed=42,
    ))
    assert response["split"] == {"train": 14, "val": 4, "test": 2}

    seen = []
    for partition, expected in (("train", 14), ("val", 4), ("test", 2)):
        gallery = routes_dataset.list_dataset_images(
            folder_path=str(tmp_path), limit=50, offset=0, split=partition, class_name=None,
        )
        assert gallery["total"] == expected
        assert {row["split"] for row in gallery["items"]} == {partition}
        seen.extend(row["file_path"] for row in gallery["items"])
    assert len(set(seen)) == 20


def test_labelme_split_excludes_unlabeled_images(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "split_manifests", raising=False)
    for index in range(10):
        image = tmp_path / f"ng_{index:02d}.jpg"
        Image.new("RGB", (32, 32)).save(image)
        if index < 8:
            (tmp_path / f"ng_{index:02d}.json").write_text(json.dumps({
                "imagePath": image.name, "imageWidth": 32, "imageHeight": 32,
                "shapes": [{"label": "Bow", "points": [[2, 2], [8, 2], [8, 8]]}],
            }))
    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(tmp_path), task="segmentation", validate_images=False,
    ))
    assert imported["source_images"] == 10
    assert imported["unlabeled_images"] == 2
    split = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(tmp_path), train_ratio=0.5, val_ratio=0.25, test_ratio=0.25,
    ))
    assert sum(split["split"].values()) == 8
    gallery = routes_dataset.list_dataset_images(folder_path=str(tmp_path), offset=0, limit=20,
                                                 split=None, class_name=None)
    assert gallery["total"] == 10
    assert sum(item["split"] == "unlabeled" for item in gallery["items"]) == 2
    assignments = routes_dataset._read_split_manifest(tmp_path)
    removed = next(path for path in assignments if Path(path).with_suffix(".json").exists())
    previous_partition = assignments[removed]
    Path(removed).with_suffix(".json").unlink()
    filtered = routes_dataset.list_dataset_images(
        folder_path=str(tmp_path), offset=0, limit=20,
        split=previous_partition, class_name=None, label_status="labeled",
    )
    assert removed not in {item["file_path"] for item in filtered["items"]}
    stale_split = routes_dataset.list_dataset_images(
        folder_path=str(tmp_path), offset=0, limit=20,
        split=previous_partition, class_name=None,
    )
    assert removed not in {item["file_path"] for item in stale_split["items"]}


def test_zero_escape_rejects_missing_real_predictions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation, "run_or_load_evaluation", lambda **kwargs: (_ for _ in ()).throw(ValueError("no model")))
    with pytest.raises(HTTPException) as error:
        routes_evaluation.get_overkill_underkill_analysis(
            job_id="qa_missing", target_max_underkill=0, cost_escape=500.0,
            cost_scrap=25.0, current_threshold=0.5,
        )
    assert error.value.status_code == 422


def test_benchmark_rejects_missing_model(monkeypatch):
    monkeypatch.setattr(routes_evaluation, "_find_model_file", lambda job_id: None)
    with pytest.raises(HTTPException) as error:
        routes_evaluation.run_inference_benchmark(routes_evaluation.BenchmarkRequest(job_id="missing"))
    assert error.value.status_code == 404


def test_flowchart_rejects_pipeline_without_trained_models(monkeypatch, tmp_path):
    image = tmp_path / "inspection.png"
    Image.new("RGB", (32, 32)).save(image)
    monkeypatch.setattr(routes_flowchart._ENGINE, "execute", lambda **kwargs: {"status": "success"})
    with pytest.raises(HTTPException) as error:
        routes_flowchart.run_flowchart(FlowchartRunRequest(pipeline=get_default_flowchart(), image_path=str(image)))
    assert error.value.status_code == 409


def test_flowchart_loads_trained_segmentation_checkpoint(tmp_path, monkeypatch):
    import torch
    from backend.engine.flowchart_engine import FlowchartEngine
    from backend.engine.segmentation.model import build_segmentation_model

    model = build_segmentation_model(num_classes=2, preset="fast", pretrained=False)
    monkeypatch.chdir(tmp_path)
    job_id = "job_123_abc123"
    checkpoint = tmp_path / "models" / job_id / "best_model.pt"
    checkpoint.parent.mkdir(parents=True)
    torch.save({"model_state_dict": model.state_dict(), "task": "segmentation",
                "classes": ["background", "defect"], "preset": "fast"}, checkpoint)
    engine = FlowchartEngine(device="cpu")
    loaded, is_trained = engine._get_inspection_model(task="segmentation", job_id=job_id)
    assert is_trained
    assert torch.equal(next(model.parameters()), next(loaded.parameters()))


def test_flowchart_rejects_wrong_model_task(tmp_path, monkeypatch):
    import torch

    checkpoint = tmp_path / "best_model.pt"
    torch.save({"task": "classification", "model_state_dict": {}}, checkpoint)
    monkeypatch.setattr(routes_flowchart._ENGINE, "_resolve_checkpoint", lambda job, task: checkpoint)
    pipeline = get_default_flowchart()
    pipeline.nodes[1].data.model_job_id = str(checkpoint)
    pipeline.nodes[2].data.model_job_id = str(checkpoint)
    with pytest.raises(HTTPException) as error:
        routes_flowchart.run_flowchart(FlowchartRunRequest(pipeline=pipeline, image_path=str(checkpoint)))
    assert error.value.status_code == 409


def test_labelme_preparation_preserves_source_and_defect_masks(tmp_path):
    from backend.engine.labelme_preparation import prepare_labelme_segmentation

    source = tmp_path / "source"
    source.mkdir()
    for index in range(4):
        image = source / f"ng_{index:04d}.jpg"
        Image.new("RGB", (512, 384), color=(90, 90, 90)).save(image)
        annotation = source / f"ng_{index:04d}.json"
        annotation.write_text(json.dumps({
            "imagePath": image.name, "imageWidth": 512, "imageHeight": 384,
            "shapes": [{"label": "Bow", "points": [[240, 190], [250, 190], [245, 200]]}],
        }), encoding="utf-8")
    Image.new("RGB", (512, 384)).save(source / "unlabelled.jpg")
    original_json = (source / "ng_0000.json").read_bytes()

    result = prepare_labelme_segmentation(source, tmp_path / "prepared", image_size=64)
    assert result["train"] == 3
    assert result["val"] == 1
    assert result["unlabelled"] == 1
    assert (source / "ng_0000.json").read_bytes() == original_json
    masks = list((tmp_path / "prepared" / "masks").glob("*/*.png"))
    assert len(masks) == 4
    assert all(np.asarray(Image.open(mask)).sum() > 0 for mask in masks)


def test_training_start_uses_prepared_labelme_dataset(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    for index in range(3):
        image = source / f"ng_{index}.jpg"
        Image.new("RGB", (128, 128), color=(60, 60, 60)).save(image)
        (source / f"ng_{index}.json").write_text(json.dumps({
            "imagePath": image.name, "imageWidth": 128, "imageHeight": 128,
            "shapes": [{"label": "Bow", "points": [[50, 50], [60, 50], [55, 60]]}],
        }), encoding="utf-8")
    prepared_counts = {}

    class ObservePreparedTrainer:
        def __init__(self, dataset_path, **kwargs):
            self.dataset_path = dataset_path

        def abort(self):
            pass

        def train(self, job_id):
            from pathlib import Path

            dataset = Path(self.dataset_path)
            prepared_counts["train"] = len(list((dataset / "images" / "train").glob("*.png")))
            prepared_counts["val"] = len(list((dataset / "masks" / "val").glob("*.png")))
            return {"status": "completed"}

    monkeypatch.setattr(routes_training, "UnifiedAutoMLTrainer", ObservePreparedTrainer)
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, "training_job_manager", manager)
    response = routes_training.start_training(routes_training.TrainingStartRequest(
        task="segmentation", dataset_path=str(source), output_dir=str(tmp_path / "models"),
        config_overrides={"epochs": 1, "image_size": 64},
    ))
    assert response["status"] == "started"
    record = manager.get_job(response["job_id"])
    assert record.dataset_path != str(source)
    record.thread.join(timeout=5)
    assert record.status == "completed"
    assert prepared_counts == {"train": 2, "val": 1}


def test_export_never_creates_a_fake_default_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "locate_checkpoint", lambda job_id: None)
    with pytest.raises(FileNotFoundError):
        exporter.export_runtime_package(job_id="missing_job", output_base_dir=tmp_path)
    assert not list(tmp_path.rglob("*.pt"))


def test_flat_ng_filenames_are_not_imported_as_ok(tmp_path):
    image = tmp_path / "ng_0001__Scratch___test.jpg"
    Image.new("RGB", (16, 16)).save(image)
    result = HierarchicalClassificationAdapter.parse_directory(tmp_path)
    assert result["classes"] == {"NG": 1}


def test_brush_mask_is_saved_as_real_mask_pixels(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_ANNOTATION_ROOTS", str(tmp_path))
    mask = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for x in range(4, 8):
        for y in range(5, 9):
            mask.putpixel((x, y), (255, 0, 0, 255))
    encoded = io.BytesIO()
    mask.save(encoded, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
    result = routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(
        image_id="brush_example", image_width=16, image_height=16, output_dir=str(tmp_path),
        annotations=[routes_annotation.AnnotationItem(
            type="brush_mask", label="Scratch", category_id=1, mask_rle=data_url,
        )],
    ))
    assert result["status"] == "saved"
    saved = np.asarray(Image.open(tmp_path / "masks" / "brush_example.png"))
    assert saved[6, 5] == 1
    assert saved[0, 0] == 0
