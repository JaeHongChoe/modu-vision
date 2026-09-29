"""Realistic flat LabelMe dataset contracts for the data stage."""

import csv
import itertools
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_dataset
from backend.engine import industrial_adapters
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.labelme_preparation import prepare_labelme_segmentation


def _flat_ng_folder(tmp_path, count=8, paired=6):
    folder = tmp_path / "flat_ng"
    folder.mkdir()
    for index in range(count):
        image = folder / f"ng_{index:04d}__Scratch___sample_view.jpg"
        Image.new("RGB", (32, 32), color=(index, 0, 0)).save(image)
        if index < paired:
            image.with_suffix(".json").write_text(json.dumps({
                "imagePath": image.name, "imageWidth": 32, "imageHeight": 32,
                "shapes": [{"label": "Bow", "shape_type": "polygon",
                            "points": [[2, 2], [8, 2], [8, 8]]}],
            }), encoding="utf-8")
    return folder


def _with_source_groups(folder, group_sizes):
    images = sorted(folder.glob("*.jpg"))
    assert len(images) == sum(group_sizes)
    groups = {}
    rows = []
    image_index = 0
    for group_index, size in enumerate(group_sizes):
        group = f"lot_01/part_{group_index:02d}"
        for local_index in range(size):
            image = images[image_index]
            groups[image.name] = group
            rows.append({
                "renamed_filename": image.name,
                "relative_source_path": f"{group}/view_{local_index:02d}.jpg",
            })
            image_index += 1
    with (folder / "flatten_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["renamed_filename", "relative_source_path"])
        writer.writeheader()
        writer.writerows(rows)
    return groups


def test_flat_labelme_import_reports_only_applied_split_and_paired_images(tmp_path, monkeypatch):
    folder = _flat_ng_folder(tmp_path)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    request = routes_dataset.DatasetImportRequest(folder_path=str(folder), task="segmentation", validate_images=False)

    initial = routes_dataset.import_dataset(request)
    assert initial["total_images"] == 6
    assert initial["source_images"] == 8
    assert initial["unlabeled_images"] == 2
    assert initial["split"] == {"train": 0, "val": 0, "test": 0}

    applied = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), train_ratio=0.5, val_ratio=0.5, test_ratio=0,
    ))
    assert applied["split"] == {"train": 3, "val": 3, "test": 0}
    reimported = routes_dataset.import_dataset(request)
    assert reimported["split"] == applied["split"]


def test_flat_labelme_split_keeps_source_groups_together_and_repeats_with_seed(tmp_path, monkeypatch):
    folder = _flat_ng_folder(tmp_path, count=16, paired=16)
    groups = _with_source_groups(folder, [5, 4, 3, 2, 1, 1])
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    request = routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), train_ratio=0.5, val_ratio=0.3125, test_ratio=0.1875, seed=17,
    )

    first = routes_dataset.split_dataset_endpoint(request)
    assignments = routes_dataset._read_split_manifest(folder)
    assert first["split"] == {"train": 8, "val": 5, "test": 3}
    assert len(assignments) == 16
    group_partitions = {}
    for image_path, partition in assignments.items():
        group_partitions.setdefault(groups[image_path.rsplit("/", 1)[-1]], set()).add(partition)
    assert all(len(partitions) == 1 for partitions in group_partitions.values())
    assert set(first["split"].values()).isdisjoint({0})

    assert routes_dataset.split_dataset_endpoint(request) == first
    assert routes_dataset._read_split_manifest(folder) == assignments


def test_studio_only_label_joins_import_grouped_split_and_training(tmp_path, monkeypatch):
    folder = _flat_ng_folder(tmp_path, count=6, paired=5)
    groups = _with_source_groups(folder, [2, 2, 2])
    annotation_root = tmp_path / "studio_annotations"
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", annotation_root)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    newly_labeled = sorted(folder.glob("*.jpg"))[-1]
    studio_folder = dataset_annotation_dir(folder, annotation_root)
    studio_folder.mkdir(parents=True)
    (studio_folder / f"{newly_labeled.stem}.json").write_text(json.dumps({
        "image_id": newly_labeled.stem,
        "annotations": [{"type": "polygon", "label": "Bow",
                         "polygon": [[2, 2], [8, 2], [8, 8]]}],
    }), encoding="utf-8")

    request = routes_dataset.DatasetImportRequest(
        folder_path=str(folder), task="segmentation", validate_images=False,
    )
    initial = routes_dataset.import_dataset(request)
    assert initial["total_images"] == 6
    assert initial["source_images"] == 6
    assert initial["unlabeled_images"] == 0

    applied = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), train_ratio=0.5, val_ratio=1 / 3, test_ratio=1 / 6,
    ))
    assignments = routes_dataset._read_split_manifest(folder)
    assert sum(applied["split"].values()) == 6
    assert len(assignments) == 6
    assert str(newly_labeled) in assignments
    group_partitions = {}
    for image_path, partition in assignments.items():
        group_partitions.setdefault(groups[image_path.rsplit("/", 1)[-1]], set()).add(partition)
    assert all(len(partitions) == 1 for partitions in group_partitions.values())

    prepared = tmp_path / "prepared"
    counts = prepare_labelme_segmentation(folder, prepared, image_size=32,
                                          assignments=assignments, annotation_root=annotation_root)
    rows = json.loads((prepared / "source_manifest.json").read_text(encoding="utf-8"))
    assert sum(counts[partition] for partition in ("train", "val", "test")) == 6
    assert all(row["split"] == assignments[row["source_image"]] for row in rows)
    assert routes_dataset.import_dataset(request)["split"] == applied["split"]


def test_empty_studio_save_removes_original_label_from_import_and_split(tmp_path, monkeypatch):
    folder = _flat_ng_folder(tmp_path, count=4, paired=4)
    annotation_root = tmp_path / "studio_annotations"
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", annotation_root)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    image = sorted(folder.glob("*.jpg"))[0]
    studio_folder = dataset_annotation_dir(folder, annotation_root)
    studio_folder.mkdir(parents=True)
    (studio_folder / f"{image.stem}.json").write_text(json.dumps({
        "image_id": image.stem, "annotations": [], "mask_file": None,
    }), encoding="utf-8")

    request = routes_dataset.DatasetImportRequest(
        folder_path=str(folder), task="segmentation", validate_images=False,
    )
    imported = routes_dataset.import_dataset(request)
    assert imported["total_images"] == 3
    assert imported["source_images"] == 4
    assert imported["unlabeled_images"] == 1
    split = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), train_ratio=0.5, val_ratio=0.5,
    ))
    assert sum(split["split"].values()) == 3
    assert str(image) not in routes_dataset._read_split_manifest(folder)


def test_flat_labelme_split_rejects_incomplete_source_manifest(tmp_path, monkeypatch):
    folder = _flat_ng_folder(tmp_path, count=6, paired=6)
    _with_source_groups(folder, [3, 2, 1])
    manifest = folder / "flatten_manifest.csv"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    manifest.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")

    with pytest.raises(HTTPException) as error:
        routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
            folder_path=str(folder), train_ratio=0.5, val_ratio=0.3, test_ratio=0.2,
        ))
    assert error.value.status_code == 422
    assert "manifest" in str(error.value.detail).lower()
    assert not routes_dataset._split_manifest_file(folder).exists()


def test_grouped_split_uses_closest_feasible_image_ratios(tmp_path, monkeypatch):
    sizes = [7, 5, 4, 3]
    folder = _flat_ng_folder(tmp_path, count=sum(sizes), paired=sum(sizes))
    _with_source_groups(folder, sizes)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    ratios = (0.7, 0.2, 0.1)

    result = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), train_ratio=ratios[0], val_ratio=ratios[1], test_ratio=ratios[2], seed=7,
    ))
    observed = tuple(result["split"][name] for name in ("train", "val", "test"))
    assert all(observed)

    candidates = []
    for assignment in itertools.product(range(3), repeat=len(sizes)):
        counts = tuple(sum(size for size, partition in zip(sizes, assignment) if partition == index)
                       for index in range(3))
        if all(counts):
            candidates.append(sum((count - sum(sizes) * ratio) ** 2
                                  for count, ratio in zip(counts, ratios)))
    observed_error = sum((count - sum(sizes) * ratio) ** 2
                         for count, ratio in zip(observed, ratios))
    assert observed_error == pytest.approx(min(candidates))


def test_flat_labelme_detection_import_and_split_use_saved_partitions(tmp_path, monkeypatch):
    folder = _flat_ng_folder(tmp_path)
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    request = routes_dataset.DatasetImportRequest(folder_path=str(folder), task="detection", validate_images=False)
    initial = routes_dataset.import_dataset(request)
    assert initial["total_images"] == 6
    assert initial["split_supported"] is True
    assert initial["split"] == {"train": 0, "val": 0, "test": 0}
    applied = routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
        folder_path=str(folder), task="detection", train_ratio=0.5, val_ratio=0.5,
    ))
    assert applied["split"] == {"train": 3, "val": 3, "test": 0}
    assert routes_dataset.import_dataset(request)["split"] == applied["split"]


@pytest.mark.parametrize("task", ["classification", "anomaly"])
def test_flat_ng_labelme_import_rejects_unsupported_training_task(tmp_path, task):
    folder = _flat_ng_folder(tmp_path)
    with pytest.raises(HTTPException) as error:
        routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
            folder_path=str(folder), task=task, validate_images=False,
        ))
    assert error.value.status_code == 422
    assert "segmentation" in str(error.value.detail).lower()


def test_industrial_anomaly_import_never_counts_flat_ng_images_as_good(tmp_path):
    folder = _flat_ng_folder(tmp_path)
    with pytest.raises(HTTPException) as error:
        routes_dataset.import_industrial_dataset_endpoint(routes_dataset.IndustrialDatasetImportRequest(
            folder_path=str(folder), task="anomaly",
        ))
    assert error.value.status_code == 422
    assert "normal" in str(error.value.detail).lower() or "ok" in str(error.value.detail).lower()


def test_labelme_lookup_does_not_fall_back_to_another_dataset(tmp_path, monkeypatch):
    requested = tmp_path / "requested"
    requested.mkdir()
    other = _flat_ng_folder(tmp_path)
    monkeypatch.setattr(industrial_adapters, "LABELME_FALLBACK_CANDIDATES", [other], raising=False)

    assert industrial_adapters.find_labelme_folder(requested) is None
