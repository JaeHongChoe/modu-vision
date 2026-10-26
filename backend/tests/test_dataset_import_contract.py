"""Realistic flat LabelMe dataset contracts for the data stage."""

import csv
import hashlib
import io
import itertools
import json
import zipfile

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_dataset
from backend.engine import industrial_adapters
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.labelme_preparation import prepare_labelme_segmentation


def test_class_split_summary_uses_all_images_before_pagination(tmp_path):
    folder = tmp_path / "presplit"
    for partition, count in (("train", 2), ("val", 1), ("test", 1)):
        for label in ("B", "C"):
            class_dir = folder / partition / label
            class_dir.mkdir(parents=True)
            for index in range(count):
                Image.new("RGB", (12, 10)).save(class_dir / f"{label}_{partition}_{index}.png")
    response = routes_dataset.list_dataset_images(
        folder_path=str(folder), task="classification", limit=1, offset=0,
        split=None, class_name=None, label_status=None,
    )
    assert response["total"] == 8
    assert len(response["items"]) == 1
    assert response["class_split_counts"] == {
        "B": {"train": 2, "val": 1, "test": 1},
        "C": {"train": 2, "val": 1, "test": 1},
    }

    filtered = routes_dataset.list_dataset_images(
        folder_path=str(folder), task="classification", limit=1, offset=0,
        split="train", class_name="B", label_status=None,
    )
    assert filtered["total"] == 2
    assert filtered.get("class_split_counts") is None


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
    assert "영역 분할" in str(error.value.detail) or "segmentation" in str(error.value.detail).lower()


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


@pytest.mark.parametrize("task", ["detection", "segmentation"])
def test_a_folder_of_images_with_no_label_yet_imports_for_detection_and_segmentation(tmp_path, monkeypatch, task):
    """A first detection or segmentation project starts from raw images: the import reads them as a flat folder with no
    labelled image yet (the app labels them in place), counts every image as source and unlabeled, and the split waits
    for labels with a Korean reason. A classification project still asks for class folders."""
    folder = tmp_path / "raw"
    folder.mkdir()
    for index in range(5):
        Image.new("RGB", (16, 12)).save(folder / f"part_{index:02d}.png")
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(folder_path=str(folder), task=task, validate_images=False))
    assert imported["source_images"] == 5
    if task == "detection":
        assert imported["total_images"] == 0
        assert imported["unlabeled_images"] == 5
        assert imported["split_supported"] is True
    else:
        # A segmentation image without a mask is a background sample: the import counts it as it always has.
        assert imported["total_images"] == 5
    with pytest.raises(HTTPException) as refused:
        routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
            folder_path=str(folder), task=task, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1))
    assert refused.value.status_code == 422 and "라벨이 있는 이미지가 아직 없어" in str(refused.value.detail)
    assert not any(folder.glob("*.json")), "nothing is written into the source folder"


@pytest.mark.parametrize("task", ["detection", "segmentation"])
def test_an_extracted_raw_image_archive_imports_without_changing_images_or_receipt(tmp_path, monkeypatch, task):
    from backend.engine.dataset_archive_input import RECEIPT, extract_dataset_archive, verify_extraction

    image = io.BytesIO()
    Image.new("RGB", (16, 12)).save(image, format="PNG")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("part.png", image.getvalue())
    archive_sha256 = hashlib.sha256(archive.getvalue()).hexdigest()
    folder = tmp_path / "extracted"
    extract_dataset_archive(archive, folder, archive_sha256=archive_sha256)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.iterdir()}
    assert set(before) == {"part.png", RECEIPT}
    monkeypatch.setattr(routes_dataset, "SPLIT_MANIFEST_DIR", tmp_path / "manifests")
    monkeypatch.setattr(routes_dataset, "STUDIO_ANNOTATIONS_DIR", tmp_path / "annotations")

    imported = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(folder), task=task, validate_images=True))

    assert imported["status"] == "success" and imported["source_images"] == 1
    assert imported["total_images"] == (0 if task == "detection" else 1)
    assert imported["unlabeled_images"] == (1 if task == "detection" else 0)
    if task == "detection":
        assert imported["labels_missing"] is True
    assert imported["validation"]["checked_images"] == 1 and imported["validation"]["complete"] is True
    with pytest.raises(HTTPException) as refused:
        routes_dataset.split_dataset_endpoint(routes_dataset.DatasetSplitRequest(
            folder_path=str(folder), task=task, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1))
    assert refused.value.status_code == 422 and "라벨이 있는 이미지가 아직 없어" in str(refused.value.detail)
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in folder.iterdir()} == before
    assert verify_extraction(folder, archive_sha256)


@pytest.mark.parametrize("extra", ["annotations_train.json", ".other.json", ".modu-extraction-backup.json", "train", "images", "masks"])
def test_the_extraction_receipt_does_not_exempt_other_json_or_structured_layouts(tmp_path, extra):
    from backend.engine.dataset_archive_input import RECEIPT

    folder = tmp_path / "raw"
    folder.mkdir()
    Image.new("RGB", (16, 12)).save(folder / "part.png")
    (folder / RECEIPT).write_text("{}")
    assert not routes_dataset._flat_image_workspace(folder, "classification")
    assert not routes_dataset._flat_image_workspace(folder, "anomaly")
    if extra.lower().endswith(".json"):
        (folder / extra).write_text("{}")
    else:
        (folder / extra).mkdir()
    assert not routes_dataset._unlabeled_image_folder(folder)


def test_a_raw_image_folder_is_not_taken_for_classification_or_a_structured_layout(tmp_path):
    folder = tmp_path / "raw"
    folder.mkdir()
    Image.new("RGB", (16, 12)).save(folder / "a.png")
    assert routes_dataset._unlabeled_image_folder(folder)
    assert not routes_dataset._flat_image_workspace(folder, "classification")
    (folder / "train").mkdir()
    assert not routes_dataset._unlabeled_image_folder(folder), "a split or class subfolder is a structured layout"
    (folder / "train").rmdir()
    (folder / "annotations_train.json").write_text("{}")
    assert not routes_dataset._unlabeled_image_folder(folder), "an annotation file is a structured layout"
