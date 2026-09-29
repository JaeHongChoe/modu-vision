"""The split shown in Step 1 must be the split consumed by training."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_dataset, routes_training
from backend.engine import dataset_loaders


def _classification_images(root: Path, physical_train: bool) -> None:
    parent = root / "train" if physical_train else root
    for label in ("OK", "NG"):
        class_dir = parent / label
        class_dir.mkdir(parents=True)
        for index in range(10):
            Image.new("RGB", (8, 8), (index, 0, 0)).save(class_dir / f"{label}_{index}.png")


@pytest.mark.parametrize("physical_train", [False, True])
def test_classification_applied_split_is_reimported_and_loaded_for_training(
    tmp_path, monkeypatch, physical_train,
):
    folder = tmp_path / "source"
    folder.mkdir()
    _classification_images(folder, physical_train)
    manifest_dir = tmp_path / "split_manifests"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", manifest_dir)
    monkeypatch.setattr(dataset_loaders, "SPLIT_MANIFEST_DIR", manifest_dir, raising=False)

    applied = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), task="classification", train_ratio=0.5,
        val_ratio=0.25, test_ratio=0.25, seed=42,
    ))["split"]
    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(folder), task="classification", validate_images=False,
    ))
    assignments = routes_dataset._read_split_manifest(folder)

    assert sum(applied.values()) == 20
    assert imported["split"] == applied
    assert len(assignments) == 20
    loaded = {}
    for partition in ("train", "val", "test"):
        dataset = dataset_loaders.ClassificationDataset(folder, split=partition)
        loaded[partition] = {str(path) for path, _ in dataset.samples}
        assert len(dataset) == applied[partition]
        assert loaded[partition] == {path for path, assigned in assignments.items() if assigned == partition}
        gallery = routes_dataset.list_dataset_images(
            folder_path=str(folder), limit=50, offset=0, split=partition, class_name=None,
        )
        assert gallery["total"] == applied[partition]
    assert not (loaded["train"] & loaded["val"] | loaded["train"] & loaded["test"] | loaded["val"] & loaded["test"])


@pytest.mark.parametrize("task", ["detection", "segmentation", "anomaly"])
def test_unsupported_task_split_rejects_without_saving_a_misleading_manifest(tmp_path, monkeypatch, task):
    folder = tmp_path / task
    folder.mkdir()
    images = folder / "images"
    images.mkdir()
    Image.new("RGB", (8, 8)).save(images / "a.png")
    if task == "segmentation":
        masks = folder / "masks"
        masks.mkdir()
        Image.new("L", (8, 8)).save(masks / "a.png")
    manifest_dir = tmp_path / "split_manifests"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", manifest_dir)

    with pytest.raises(HTTPException) as error:
        routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
            folder_path=str(folder), task=task, train_ratio=0.5,
            val_ratio=0.25, test_ratio=0.25,
        ))

    assert error.value.status_code == 422
    assert not routes_dataset._split_manifest_file(folder).exists()


@pytest.mark.parametrize("nested_task_dir", [False, True])
def test_classification_import_and_training_start_use_same_source_folder(
    tmp_path, monkeypatch, nested_task_dir,
):
    selected = tmp_path / "selected"
    selected.mkdir()
    dataset_root = selected / "classification" if nested_task_dir else selected
    _classification_images(dataset_root, physical_train=True)
    for label in ("OK", "NG"):
        val_dir = dataset_root / "val" / label
        val_dir.mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(val_dir / f"val_{label}.png")
    import_result = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(selected), task="classification", validate_images=False,
    ))
    captured = {}

    def capture_job(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", capture_job)
    started = routes_training.start_training(routes_training.TrainingStartRequest(
        dataset_path=str(selected), task="classification", output_dir=str(tmp_path / "models"),
    ))

    assert started["status"] == "started"
    assert import_result["total_images"] == 22
    assert Path(captured["dataset_path"]) == dataset_root
    assert len(dataset_loaders.ClassificationDataset(captured["dataset_path"], split="train")) == 20


def test_physical_classification_test_is_never_used_as_missing_validation(tmp_path, monkeypatch):
    root = tmp_path / "classification"
    for partition in ("train", "test"):
        for label in ("OK", "NG"):
            folder = root / partition / label
            folder.mkdir(parents=True)
            Image.new("RGB", (8, 8)).save(folder / f"{partition}_{label}.png")
    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(root), task="classification", validate_images=False,
    ))
    assert imported["split"] == {"train": 2, "val": 0, "test": 2}
    assert len(dataset_loaders.ClassificationDataset(root, split="val")) == 0

    def never_start(**_kwargs):
        raise AssertionError("training must not start without validation images")

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", never_start)
    with pytest.raises(HTTPException) as error:
        routes_training.start_training(routes_training.TrainingStartRequest(
            dataset_path=str(root), task="classification", output_dir=str(tmp_path / "models"),
        ))
    assert error.value.status_code == 422
    assert "val" in str(error.value.detail).lower()


@pytest.mark.parametrize("nested_task_dir", [False, True])
def test_stale_classification_manifest_can_be_reimported_and_resplit(
    tmp_path, monkeypatch, nested_task_dir,
):
    selected = tmp_path / "selected"
    selected.mkdir()
    root = selected / "classification" if nested_task_dir else selected
    _classification_images(root, physical_train=nested_task_dir)
    manifest_dir = tmp_path / "split_manifests"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", manifest_dir)
    monkeypatch.setattr(dataset_loaders, "SPLIT_MANIFEST_DIR", manifest_dir, raising=False)
    request = routes_dataset.DatasetSplitRequest(
        folder_path=str(selected), task="classification", train_ratio=0.5,
        val_ratio=0.25, test_ratio=0.25, seed=42,
    )
    routes_dataset.split_dataset_endpoint(request)
    new_image_dir = root / "train" / "OK" if nested_task_dir else root / "OK"
    Image.new("RGB", (8, 8)).save(new_image_dir / "new.png")

    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(selected), task="classification", validate_images=False,
    ))

    assert imported["total_images"] == 21
    assert imported["classes"] == {"OK": 11, "NG": 10}
    assert imported["split"] == {"train": 0, "val": 0, "test": 0}
    with pytest.raises(ValueError, match="Saved split"):
        dataset_loaders.ClassificationDataset(root, split="train")

    applied = routes_dataset.split_dataset_endpoint(request)["split"]
    restored = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(selected), task="classification", validate_images=False,
    ))
    assert sum(applied.values()) == 21
    assert restored["split"] == applied


def test_task_folder_resolution_does_not_select_unrelated_nested_dataset(tmp_path):
    selected = tmp_path / "selected"
    (selected / "detection" / "train").mkdir(parents=True)

    assert routes_dataset._resolve_task_folder(selected, "classification") == selected


def test_nested_classification_split_survives_reimport_and_training_loader(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    nested = selected / "classification"
    _classification_images(nested, physical_train=True)
    manifest_dir = tmp_path / "split_manifests"
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", manifest_dir)
    monkeypatch.setattr(dataset_loaders, "SPLIT_MANIFEST_DIR", manifest_dir, raising=False)

    applied = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(selected), task="classification", train_ratio=0.5,
        val_ratio=0.25, test_ratio=0.25, seed=42,
    ))["split"]
    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(selected), task="classification", validate_images=False,
    ))

    assert imported["split"] == applied
    assert {
        partition: len(dataset_loaders.ClassificationDataset(nested, split=partition))
        for partition in ("train", "val", "test")
    } == applied


def test_existing_physical_splits_keep_one_class_index_mapping(tmp_path):
    root = tmp_path / "classification"
    for partition, label in (("train", "OK"), ("train", "NG"), ("val", "NG")):
        folder = root / partition / label
        folder.mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(folder / f"{partition}_{label}.png")

    train = dataset_loaders.ClassificationDataset(root, split="train")
    val = dataset_loaders.ClassificationDataset(root, split="val")

    assert train.class_to_idx == val.class_to_idx == {"OK": 0, "NG": 1}
    assert [label_idx for _, label_idx in val.samples] == [1]


def _coco_json(image_name: str) -> dict:
    return {
        "images": [{"id": 1, "file_name": image_name, "width": 8, "height": 8}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 4, 4]}],
        "categories": [{"id": 1, "name": "defect"}],
    }


def test_single_coco_file_has_no_validation_split_and_cannot_start_training(tmp_path):
    root = tmp_path / "detection"
    images = root / "images"
    images.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(images / "one.png")
    (root / "annotations.json").write_text(json.dumps(_coco_json("one.png")), encoding="utf-8")

    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(root), task="detection", validate_images=False,
    ))
    assert imported["split"] == {"train": 1, "val": 0, "test": 0}

    with pytest.raises(HTTPException) as error:
        routes_training.start_training(routes_training.TrainingStartRequest(
            dataset_path=str(root), task="detection", output_dir=str(tmp_path / "models"),
        ))
    assert error.value.status_code == 422
    assert "train" in str(error.value.detail) and "val" in str(error.value.detail)


def test_separate_coco_train_and_val_remain_trainable(tmp_path, monkeypatch):
    root = tmp_path / "detection"
    for partition in ("train", "val"):
        images = root / "images" / partition
        images.mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(images / f"{partition}.png")
        (root / f"annotations_{partition}.json").write_text(
            json.dumps(_coco_json(f"{partition}.png")), encoding="utf-8",
        )
    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(root), task="detection", validate_images=False,
    ))
    assert imported["split"] == {"train": 1, "val": 1, "test": 0}
    captured = {}

    def capture_job(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", capture_job)
    started = routes_training.start_training(routes_training.TrainingStartRequest(
        dataset_path=str(root), task="detection", output_dir=str(tmp_path / "models"),
    ))

    assert started["status"] == "started"
    assert Path(captured["dataset_path"]) == root


def test_nested_coco_annotation_files_are_rejected_at_import_until_loader_supports_them(tmp_path):
    root = tmp_path / "detection"
    annotations = root / "annotations"
    annotations.mkdir(parents=True)
    for partition in ("train", "val"):
        images = root / "images" / partition
        images.mkdir(parents=True)
        Image.new("RGB", (8, 8)).save(images / f"{partition}.png")
        (annotations / f"annotations_{partition}.json").write_text(
            json.dumps(_coco_json(f"{partition}.png")), encoding="utf-8",
        )

    with pytest.raises(HTTPException) as error:
        routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
            folder_path=str(root), task="detection", validate_images=False,
        ))

    assert error.value.status_code == 422
    assert "annotations_train.json" in str(error.value.detail)
    assert "annotations_val.json" in str(error.value.detail)


def test_labelme_linked_subset_uses_the_saved_split_during_preparation(tmp_path, monkeypatch):
    original = tmp_path / "original"
    linked = tmp_path / "linked"
    original.mkdir()
    linked.mkdir()
    for index in range(4):
        image = original / f"sample_{index}.jpg"
        Image.new("RGB", (16, 16), (index * 20, 0, 0)).save(image)
        annotation = original / f"sample_{index}.json"
        annotation.write_text(json.dumps({
            "imagePath": image.name,
            "imageWidth": 16,
            "imageHeight": 16,
            "shapes": [{"label": "Bow", "shape_type": "polygon", "points": [[2, 2], [8, 2], [8, 8]]}],
        }), encoding="utf-8")
        (linked / image.name).symlink_to(image)
        (linked / annotation.name).symlink_to(annotation)

    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "split_manifests")
    applied = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(linked), task="segmentation", train_ratio=0.5,
        val_ratio=0.5, test_ratio=0,
    ))["split"]
    reimported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(linked), task="segmentation", validate_images=False,
    ))
    assert reimported["split"] == applied
    captured = {}

    def capture_job(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", capture_job)
    started = routes_training.start_training(routes_training.TrainingStartRequest(
        dataset_path=str(linked), task="segmentation", output_dir=str(tmp_path / "models"),
        config_overrides={"image_size": 64},
    ))
    captured["prepare_dataset"](threading.Event())
    prepared = Path(captured["dataset_path"])

    assert started["status"] == "started"
    assert applied == {"train": 2, "val": 2, "test": 0}
    assert len(list((prepared / "images" / "train").glob("*.png"))) == applied["train"]
    assert len(list((prepared / "images" / "val").glob("*.png"))) == applied["val"]
